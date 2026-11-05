"""Tests voor losse extra carpooldagen (bijv. een vrijdag)."""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rare_acties import A, FUT, PAST, TODAY, fresh, login, post, text  # noqa: E402

FRI = FUT + timedelta(days=4)   # vrijdag na de toekomstige maandag; geen vaste carpooldag


def extra_rows():
    with A.app.app_context():
        return [tuple(r) for r in A.db().execute('SELECT ride_date, created_by FROM extra_days')]


def test_iedereen_kan_extra_dag_toevoegen_en_gaat_zelf_mee():
    fresh()
    bas, _ = login('Bas')
    r = post(bas, '/extra-day', day=FRI.isoformat())
    assert r.status_code == 302 and f'#day-{FRI}' in r.headers['Location']
    assert extra_rows() == [(FRI.isoformat(), 2)]
    page = text(bas.get(r.headers['Location'].split('#')[0]))
    assert 'Extra dag' in page and f'id="day-{FRI}"' in page
    assert 'wil ook op' not in page, 'wie hem toevoegt krijgt zelf geen uitnodiging'
    # Anna en Chris krijgen een melding
    anna, _ = login('Anna')
    assert 'Bas wil ook op vrijdag' in text(anna.get('/'))
    post(anna, f'/attendance/{FRI}', going='1')
    assert 'Bas wil ook op vrijdag' not in text(anna.get('/')), 'melding weg na reageren'


def test_extra_dag_krijgt_chauffeur_en_staat_in_agenda():
    fresh()
    bas, _ = login('Bas')
    post(bas, '/extra-day', day=FRI.isoformat())
    chris, _ = login('Chris')
    post(chris, f'/attendance/{FRI}', going='1')
    with A.app.test_request_context():
        day = next(d for d in A.plan_for(FRI) if d['iso'] == FRI.isoformat())
        token = A.calendar_token(A.find_user('Chris'))
    assert day['extra'] and day['driver'] in (2, 3) and len(day['present']) == 2
    feed = text(chris.get(f'/agenda/{token}.ics'))
    assert f'DTSTART;VALUE=DATE:{FRI:%Y%m%d}' in feed


def test_vaste_dag_of_ongeldige_datum():
    fresh()
    bas, _ = login('Bas')
    r = post(bas, '/extra-day', day=FUT.isoformat())          # maandag is al een carpooldag
    assert extra_rows() == [] and f'#day-{FUT}' in r.headers['Location']
    for bad in ('', 'gisteren', (TODAY - timedelta(days=1)).isoformat(), (TODAY + timedelta(days=400)).isoformat()):
        assert post(bas, '/extra-day', day=bad).status_code == 302
    assert extra_rows() == []
    # niet-carpooldag zonder extra dag blijft geweigerd
    assert post(bas, f'/attendance/{FRI}', going='1').status_code == 400


def test_weghalen_alleen_zolang_niemand_anders_meegaat():
    fresh()
    bas, _ = login('Bas')
    post(bas, '/extra-day', day=FRI.isoformat())
    chris, _ = login('Chris')
    post(chris, f'/attendance/{FRI}', going='1')
    post(bas, f'/extra-day/{FRI}/remove')
    assert extra_rows(), 'Chris gaat mee, dus Bas kan hem niet weghalen'
    post(chris, f'/attendance/{FRI}', going='0')
    post(bas, f'/extra-day/{FRI}/remove')
    assert extra_rows() == []
    with A.app.app_context():
        assert A.db().execute('SELECT COUNT(*) FROM attendance WHERE ride_date=?', (FRI.isoformat(),)).fetchone()[0] == 0
    assert post(bas, f'/attendance/{FRI}', going='1').status_code == 400
    actions = [r for r in text(login('Anna')[0].get('/audit')).split('audit-row')]
    assert any('Extra dag toegevoegd' in a for a in actions) and any('Extra dag verwijderd' in a for a in actions)


def test_beheerder_mag_altijd_weghalen():
    fresh()
    bas, _ = login('Bas')
    post(bas, '/extra-day', day=FRI.isoformat())
    chris, _ = login('Chris')
    post(chris, f'/attendance/{FRI}', going='1')
    anna, _ = login('Anna')
    page = text(anna.get('/'))
    # Bovenaan in het Extra dag-paneel: lijst met verwijderknop en een bevestigingsvraag die waarschuwt
    assert 'Geplande extra dagen' in page and '1 gepland' in page
    assert f'/extra-day/{FRI}/remove' in page and 'Weet je zeker dat je de extra dag op vrijdag' in page
    assert 'Bas en Chris gaan al mee en krijgen dan geen rit' in page
    post(anna, f'/extra-day/{FRI}/remove')
    assert extra_rows() == []


def test_verwijderknop_bovenaan_voor_wie_hem_toevoegde():
    fresh()
    bas, _ = login('Bas')
    post(bas, '/extra-day', day=FRI.isoformat())
    page = text(bas.get('/'))
    assert 'Geplande extra dagen' in page and 'data-confirm="Weet je zeker dat je de extra dag op vrijdag' in page
    chris, _ = login('Chris')
    post(chris, f'/attendance/{FRI}', going='1')
    page = text(bas.get('/'))
    assert f'/extra-day/{FRI}/remove' not in page and 'Chris gaat al mee' in page


def test_afgeronde_extra_dag_telt_mee_en_blijft_staan():
    fresh()
    with A.app.app_context():
        A.db().execute("INSERT INTO extra_days(ride_date,created_by,created_at) VALUES (?,2,'x')", (PAST.isoformat(),))
        A.db().execute("UPDATE settings SET value='0' WHERE key='weekdays'")   # donderdag tijdelijk geen vaste dag
        A.db().commit()
    bas, _ = login('Bas')
    post(bas, f'/confirm/{PAST}', driver='2', passengers='3')
    with A.app.test_request_context():
        assert A.stats().drives[2] == 1
    assert post(bas, f'/extra-day/{PAST}/remove').status_code == 400


def test_te_veel_extra_dagen():
    fresh()
    with A.app.app_context():
        for i in range(A.MAX_EXTRA_DAYS):
            A.db().execute("INSERT INTO extra_days(ride_date,created_by,created_at) VALUES (?,1,'x')",
                           ((TODAY + timedelta(days=200 + i)).isoformat(),))
        A.db().commit()
    bas, _ = login('Bas')
    post(bas, '/extra-day', day=FRI.isoformat())
    assert FRI.isoformat() not in [r[0] for r in extra_rows()]


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
