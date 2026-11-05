"""Tests voor 'Gewijzigd'-meldingen, vergeten ritten en 'volgende keer aan de beurt'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rare_acties import A, FUT, PAST, fresh, login, post, text  # noqa: E402

FUT2 = FUT + __import__('datetime').timedelta(days=3)   # de donderdag daarna


def everyone_goes(*days):
    for name in ('Anna', 'Bas', 'Chris'):
        c, _ = login(name)
        for d in days:
            post(c, f'/attendance/{d}', going='1')


def test_eerste_bezoek_geen_wijzigingen():
    fresh()
    everyone_goes(FUT, FUT2)
    c, _ = login('Anna')
    assert 'Gewijzigd sinds' not in text(c.get('/'))


def test_wijziging_door_ander_wordt_getoond_en_daarna_weg():
    fresh()
    everyone_goes(FUT, FUT2)
    anna, _ = login('Anna')
    page = text(anna.get('/'))
    assert 'Anna (jij)' in page                  # Anna rijdt de eerste dag (accountvolgorde)
    bas, _ = login('Bas')
    bas.get('/')
    # Anna kan de eerste dag niet: dan rijdt een ander
    post(anna, f'/attendance/{FUT}', going='0')
    page = text(anna.get('/'))
    assert 'Gewijzigd sinds je laatste bezoek' in page
    assert 'jij hoeft niet meer te rijden' in page
    assert 'status changed' in page
    # Bas ziet dat zij nu rijdt
    page = text(bas.get('/'))
    assert 'jij rijdt nu' in page, page[page.find('notice'):][:600]
    # Blijft staan bij opnieuw kijken (binnen 24 uur)
    assert 'jij rijdt nu' in text(bas.get('/'))


def test_terug_naar_oude_stand_geen_gewijzigd():
    fresh()
    everyone_goes(FUT, FUT2)
    anna, _ = login('Anna')
    bas, _ = login('Bas')
    anna.get('/'); bas.get('/')
    post(anna, f'/attendance/{FUT}', going='0')       # per ongeluk Nee...
    assert 'Gewijzigd sinds' in text(anna.get('/'))
    post(anna, f'/attendance/{FUT}', going='1')       # ...en weer Ja
    assert 'Gewijzigd sinds' not in text(anna.get('/'))
    assert 'status changed' not in text(anna.get('/'))
    assert 'Gewijzigd sinds' not in text(bas.get('/')), 'Bas heeft de tussenstap nooit gezien'


def test_wijziging_verdwijnt_na_24_uur():
    fresh()
    everyone_goes(FUT, FUT2)
    anna, _ = login('Anna')
    anna.get('/')
    post(anna, f'/attendance/{FUT}', going='0')
    assert 'Gewijzigd sinds' in text(anna.get('/'))
    with A.app.app_context():
        A.db().execute("UPDATE seen_plan SET shown_at='2000-01-01T00:00:00' WHERE shown_at IS NOT NULL")
        A.db().commit()
    assert 'Gewijzigd sinds' not in text(anna.get('/'))
    post(anna, f'/attendance/{FUT}', going='1')       # nu is terugdraaien wél een wijziging
    assert 'jij rijdt nu' in text(anna.get('/'))


def test_vergeten_rit_melding_blijft_tot_ingevuld():
    fresh()
    with A.app.app_context():
        for uid in (1, 2, 3):
            A.db().execute('INSERT INTO attendance(user_id,ride_date,going) VALUES (?,?,1)', (uid, PAST.isoformat()))
        A.db().commit()
    c, _ = login('Chris')
    for _ in range(3):                            # blijft staan, ook bij herhaald bezoek
        assert 'Nog invullen: wie reed er' in text(c.get('/'))
    post(c, f'/confirm/{PAST}', driver='3', passengers=['1', '2'])
    assert 'Nog invullen' not in text(c.get('/'))


def test_alleen_een_persoon_is_geen_carpool():
    fresh()
    with A.app.app_context():
        A.db().execute('INSERT INTO attendance(user_id,ride_date,going) VALUES (2,?,1)', (PAST.isoformat(),))
        A.db().commit()
    bas, _ = login('Bas')
    post(bas, f'/attendance/{FUT}', going='1')
    page = text(bas.get('/')) + text(bas.get('/?week=-1'))
    assert 'Nog invullen' not in page, 'alleen gereden hoeft niet ingevuld te worden'
    assert 'Geen carpool' in page and 'Alleen jij gaat' in page
    assert 'Jij rijdt' not in page and 'Bas (jij)' not in page
    anna, _ = login('Anna')
    page = text(anna.get('/'))
    assert 'Alleen Bas gaat' in page and 'Bas rijdt' not in page
    with A.app.test_request_context():
        token = A.calendar_token(A.find_user('Bas'))
    assert 'BEGIN:VEVENT' not in text(bas.get(f'/agenda/{token}.ics')), 'geen carpool, geen agenda-afspraak'
    # Gaat er iemand bij, dan is het weer een gewone carpooldag
    post(anna, f'/attendance/{FUT}', going='1')
    page = text(anna.get('/'))
    assert 'Geen carpool' not in page.split('class="toolbar"')[0]


def test_wordt_het_een_solodag_dan_zegt_de_melding_dat():
    fresh()
    for name in ('Anna', 'Bas'):
        c, _ = login(name)
        post(c, f'/attendance/{FUT}', going='1')
    anna, _ = login('Anna')
    assert 'Anna (jij)' in text(anna.get('/'))
    bas, _ = login('Bas')
    post(bas, f'/attendance/{FUT}', going='0')
    page = text(anna.get('/'))
    assert 'jij hoeft niet meer te rijden</strong>, geen carpool (alleen jij gaat)' in page


def test_er_is_niet_gereden_haalt_melding_weg():
    fresh()
    with A.app.app_context():
        for uid in (1, 2):
            A.db().execute('INSERT INTO attendance(user_id,ride_date,going) VALUES (?,?,1)', (uid, PAST.isoformat()))
        A.db().commit()
    c, _ = login('Bas')
    assert 'Nog invullen' in text(c.get('/'))
    assert post(c, f'/no-ride/{PAST}').status_code == 302
    assert 'Nog invullen' not in text(c.get('/'))
    assert 'Niet gereden</span>' in text(c.get('/?week=-1')) + text(c.get('/'))
    assert post(c, f'/no-ride/{FUT}').status_code == 400   # toekomst kan niet


def test_vergeten_rit_van_voor_startdatum_geen_melding():
    fresh()
    with A.app.app_context():
        A.db().execute('INSERT INTO attendance(user_id,ride_date,going) VALUES (1,?,1)', (PAST.isoformat(),))
        A.db().execute("INSERT INTO settings(key,value) VALUES ('count_from',?)", (FUT.isoformat(),))
        A.db().commit()
    c, _ = login('Bas')
    assert 'Nog invullen' not in text(c.get('/'))


def test_volgende_rit_bovenaan():
    fresh()
    c, _ = login('Chris')
    assert 'Nog niemand aangemeld' in text(c.get('/'))
    from datetime import timedelta
    from test_rare_acties import TODAY
    first = next(TODAY + timedelta(days=i) for i in range(7) if (TODAY + timedelta(days=i)).weekday() in (0, 3))
    second = next(first + timedelta(days=i) for i in range(1, 8) if (first + timedelta(days=i)).weekday() in (0, 3))
    everyone_goes(first, second)
    page = text(c.get('/'))
    top = page[page.index('next-ride'):page.index('class="toolbar"')]
    assert 'Anna rijdt' in top and 'Mee: Bas, Chris' in top
    assert 'Daarna' in top and 'Je gaat' in top          # korte agenda met eigen status
    assert top.count('Jij rijdt') <= 1 or 'pill drive' not in top, 'geen dubbel Jij rijdt'
    r = post(c, f'/attendance/{second}', going='0', **{'from': 'top'})
    assert r.location.endswith('/'), 'knop bovenaan springt niet naar beneden'


def test_telt_niet_mee_is_grijs():
    fresh()
    c, _ = login('Anna')
    post(c, f'/confirm/{PAST}', driver='1', passengers='2')
    with A.app.app_context():
        A.db().execute("INSERT INTO settings(key,value) VALUES ('count_from',?)", (FUT.isoformat(),))
        A.db().commit()
    page = text(c.get('/?week=-1')) + text(c.get('/'))
    assert '<span class="status">Telt niet mee</span>' in page


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
