"""Tests: aanwezigheid van een collega aanpassen (als die het vergeten is)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rare_acties import A, FUT, PAST, fresh, login, post, text  # noqa: E402
from test_push import at, run, subscribe, titles_for  # noqa: E402


def going(day):
    with A.app.app_context():
        return dict(A.db().execute('SELECT user_id, going FROM attendance WHERE ride_date=?', (day.isoformat(),)).fetchall())


def set_by(day):
    with A.app.app_context():
        return {r[0]: r[1] for r in A.db().execute('SELECT user_id, set_by FROM attendance_set_by WHERE ride_date=?',
                                                   (day.isoformat(),))}


def test_collega_aanmelden_en_afmelden():
    fresh()
    bas, _ = login('Bas')
    page = text(bas.get('/'))
    assert 'Aanpassen voor een collega' in page
    assert 'name="user" value="1"' in page and 'name="user" value="2"' not in page, 'jezelf staat er niet in'
    r = post(bas, f'/attendance/{FUT}', going='1', user='1')
    assert r.status_code == 302 and going(FUT) == {1: 1} and set_by(FUT) == {1: 2}
    assert 'Anna staat nu op' in text(bas.get('/'))
    page = text(bas.get('/'))
    assert 'Anna<small class="set-by"' in page and '· door jou' in page
    # Anna ziet wie het deed en kan het zelf aanpassen; dan is het weer haar eigen keuze.
    anna, _ = login('Anna')
    assert 'Bas heeft je aangemeld voor' in text(anna.get('/'))
    post(anna, f'/attendance/{FUT}', going='1')
    assert set_by(FUT) == {} and 'heeft je aangemeld' not in text(anna.get('/'))
    # Afmelden voor een ander kan ook.
    post(bas, f'/attendance/{FUT}', going='0', user='1')
    assert going(FUT)[1] == 0
    assert 'Bas heeft je afgemeld voor' in text(anna.get('/'))


def test_telt_mee_in_planning_en_auditlog():
    fresh()
    bas, _ = login('Bas')
    post(bas, f'/attendance/{FUT}', going='1')
    post(bas, f'/attendance/{FUT}', going='1', user='3')
    with A.app.test_request_context():
        day = next(d for d in A.plan_for(FUT) if d['iso'] == FUT.isoformat())
    assert {u['id'] for u in day['present']} == {2, 3} and day['driver'] in (2, 3)
    with A.app.app_context():
        row = A.db().execute("SELECT user_id, detail FROM audit_log WHERE action='attendance_other'").fetchone()
    assert row[0] == 2 and 'Chris gaat' in row[1]


def test_ongeldig_of_verleden():
    fresh()
    bas, _ = login('Bas')
    assert post(bas, f'/attendance/{FUT}', going='1', user='999').status_code == 400
    assert post(bas, f'/attendance/{FUT}', going='1', user='x').status_code == 400
    assert post(bas, f'/attendance/{PAST}', going='1', user='1').status_code == 400
    assert A.app.test_client().post(f'/attendance/{FUT}', data={'going': '1', 'user': '1'}).status_code in (302, 400)
    assert going(FUT) == {}


def test_melding_naar_collega():
    fresh()
    _, ea = subscribe('Anna')
    _, eb = subscribe('Bas')
    run(at(12))
    bas, _ = login('Bas')
    post(bas, f'/attendance/{FUT}', going='1', user='1')
    sent = run(at(12), actors={2})
    titles = titles_for(sent, ea)
    assert any(t.startswith('＋ Bas heeft je aangemeld voor') for t in titles), titles
    assert not titles_for(sent, eb), 'wie het aanpast krijgt zelf geen melding'
    assert not [t for t in titles_for(run(at(12)), ea) if 'heeft je' in t], 'niet dubbel'
    # Opnieuw aangepast (afgemeld): nieuwe melding.
    import time
    time.sleep(1.1)
    post(bas, f'/attendance/{FUT}', going='0', user='1')
    assert any(t.startswith('Bas heeft je afgemeld voor') for t in titles_for(run(at(12), actors={2}), ea))


def test_oude_tabel_wordt_overgezet():
    fresh()
    with A.app.app_context():
        con = A.db()
        con.execute('CREATE TABLE extra_day_guests (ride_date TEXT, user_id INTEGER, added_by INTEGER, PRIMARY KEY (ride_date, user_id))')
        con.execute('INSERT INTO extra_day_guests VALUES (?,1,2)', (FUT.isoformat(),))
        con.commit()
        A.init_db()
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='extra_day_guests'").fetchone()
    assert set_by(FUT) == {1: 2}


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and fn.__module__ == '__main__':
            try:
                fn()
                print('OK   ', name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print('FOUT ', name, '->', type(e).__name__, e)
    print(f'\n{failed} test(s) mislukt')
    sys.exit(1 if failed else 0)
