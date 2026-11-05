"""Tests voor het agenda-abonnement (iCalendar-feed)."""
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rare_acties import A, PAST, TODAY, fresh, login, post, text  # noqa: E402
from test_meldingen import everyone_goes  # noqa: E402

DAYS = [TODAY + timedelta(days=i) for i in range(1, 30) if (TODAY + timedelta(days=i)).weekday() in (0, 3)]


def feed_url(c):
    page = text(c.get('/agenda'))
    return re.search(r'id="cal-url" type="text" readonly value="([^"]+)"', page).group(1)


def unfold(ics):
    return ics.replace('\r\n ', '')


def events(ics):
    return re.findall(r'BEGIN:VEVENT\r\n(.*?)END:VEVENT', unfold(ics), re.S)


def test_agendapagina_en_link():
    fresh()
    c, _ = login('Anna')
    page = text(c.get('/agenda'))
    url = feed_url(c)
    assert url.startswith('http://localhost/agenda/') and url.endswith('.ics')
    assert 'webcal://localhost/agenda/' in page and 'calendar.google.com' in page
    assert feed_url(c) == url, 'link blijft hetzelfde'


def test_feed_werkt_zonder_inloggen_en_bevat_juiste_dagen():
    fresh()
    everyone_goes(DAYS[0], DAYS[1])
    anna, _ = login('Anna')
    url = feed_url(anna).replace('http://localhost', '')
    r = A.app.test_client().get(url)               # agenda-app: geen cookie
    assert r.status_code == 200 and r.mimetype == 'text/calendar'
    ics = r.get_data(as_text=True)
    assert ics.startswith('BEGIN:VCALENDAR\r\n') and ics.endswith('END:VCALENDAR\r\n')
    assert 'REFRESH-INTERVAL;VALUE=DURATION:PT12H' in ics
    ev = events(ics)
    assert len(ev) == 2
    assert f'DTSTART;VALUE=DATE:{DAYS[0]:%Y%m%d}' in ev[0]
    assert all(len(line.encode()) <= 75 for line in ics.split('\r\n')), 'regels gevouwen volgens RFC 5545'
    assert 'Jij rijdt' in ev[0] and 'Je neemt mee: Bas\\, Chris' in ev[0]
    assert 'rijdt (jij rijdt mee)' in ev[1]


def test_alleen_dagen_waarop_je_gaat():
    fresh()
    everyone_goes(DAYS[0], DAYS[1])
    chris, _ = login('Chris')
    post(chris, f'/attendance/{DAYS[1]}', going='0')
    ics = A.app.test_client().get(feed_url(chris).replace('http://localhost', '')).get_data(as_text=True)
    assert len(events(ics)) == 1


def test_afgeronde_rit_staat_erin():
    fresh()
    anna, _ = login('Anna')
    post(anna, f'/confirm/{PAST}', driver='2', passengers=['1'])
    ics = A.app.test_client().get(feed_url(anna).replace('http://localhost', '')).get_data(as_text=True)
    ev = events(ics)
    assert any('✓ Bas reed (jij reed mee)' in e and 'STATUS:CONFIRMED' in e for e in ev)


def test_ongeldige_of_vernieuwde_link():
    fresh()
    c = A.app.test_client()
    assert c.get('/agenda/kort.ics').status_code == 404
    assert c.get('/agenda/' + 'x' * 32 + '.ics').status_code == 404
    anna, _ = login('Anna')
    old = feed_url(anna).replace('http://localhost', '')
    assert c.get(old).status_code == 200
    post(anna, '/agenda', action='renew')
    new = feed_url(anna).replace('http://localhost', '')
    assert new != old
    assert c.get(old).status_code == 404 and c.get(new).status_code == 200


def test_agendapagina_vereist_login():
    fresh()
    assert A.app.test_client().get('/agenda').status_code == 302


def test_speciale_tekens_in_naam_worden_ge_escaped():
    fresh()
    with A.app.app_context():
        A.db().execute("UPDATE users SET name='Bas; de, Boer' WHERE id=2")
        A.db().commit()
    everyone_goes_names = ('Anna', 'Bas; de, Boer', 'Chris')
    for name in everyone_goes_names:
        c, _ = login(name)
        post(c, f'/attendance/{DAYS[0]}', going='1')
    anna, _ = login('Anna')
    ics = A.app.test_client().get(feed_url(anna).replace('http://localhost', '')).get_data(as_text=True)
    assert 'Bas\\; de\\, Boer' in unfold(ics)


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
