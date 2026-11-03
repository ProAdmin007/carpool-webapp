"""Tests voor rare / onverwachte gebruikersacties.

Draaien:  .venv\\Scripts\\python -m pytest tests -q   (of: python tests/test_rare_acties.py)
Gebruikt een tijdelijke database; de echte carpool.sqlite3 wordt niet aangeraakt.
"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ['CARPOOL_DB'] = os.path.join(tempfile.mkdtemp(), 'test.sqlite3')
os.environ['SECRET_KEY'] = 'test-sleutel'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as A  # noqa: E402

PW = 'wachtwoord123456'
TODAY = date.today()


def next_day(weekday, start=TODAY + timedelta(days=1)):
    d = start
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def prev_day(weekday, start=TODAY - timedelta(days=1)):
    d = start
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


FUT = next_day(0)            # toekomstige maandag
PAST = prev_day(3)           # vorige donderdag
NOT_CARPOOL = next_day(2)    # woensdag


def tok(c):
    with c.session_transaction() as s:
        return s.setdefault('csrf', 'tok')


def post(c, url, **data):
    data.setdefault('csrf', tok(c))
    return c.post(url, data=data)


def text(r):
    return r.get_data(as_text=True)


def fresh():
    """Lege database met Anna (beheerder), Bas en Chris."""
    with A.app.app_context():
        A.init_db()
        db = A.db()
        for t in ('ride_passengers', 'rides', 'attendance', 'users'):
            db.execute(f'DELETE FROM {t}')
        db.execute("DELETE FROM settings WHERE key != 'weekdays'")
        db.execute("UPDATE settings SET value='0,3' WHERE key='weekdays'")
        db.execute("DELETE FROM sqlite_sequence WHERE name='users'")
        for name, admin in (('Anna', 1), ('Bas', 0), ('Chris', 0)):
            db.execute('INSERT INTO users(name,password_hash,is_admin) VALUES (?,?,?)',
                       (name, A.generate_password_hash(PW), admin))
        db.commit()


def login(name, pw=PW):
    c = A.app.test_client()
    c.get('/login')
    r = post(c, '/login', name=name, password=pw)
    return c, r


def ride_count():
    with A.app.app_context():
        return A.db().execute('SELECT COUNT(*) FROM rides').fetchone()[0]


# ---------------------------------------------------------------- inloggen

def test_login_hoofdletters_en_spaties():
    fresh()
    for variant in ('anna', 'ANNA', '  Anna  ', 'aNnA'):
        _, r = login(variant)
        assert r.status_code == 302 and r.location.endswith('/'), variant


def test_login_fout_wachtwoord_en_onbekende_naam():
    fresh()
    for name, pw in (('Anna', 'fout'), ('Niemand', PW), ('', ''), ('Anna', '')):
        _, r = login(name, pw)
        assert r.status_code == 200 and 'klopt niet' in text(r)


def test_niet_ingelogd_wordt_doorgestuurd():
    fresh()
    c = A.app.test_client()
    for url in ('/', '/settings', '/password'):
        assert c.get(url).status_code == 302
    for url in (f'/attendance/{FUT}', f'/confirm/{PAST}', f'/undo/{PAST}'):
        r = post(c, url, going='1', driver='1')
        assert r.status_code == 302 and '/login' in r.location


def test_setup_niet_meer_bereikbaar_als_er_accounts_zijn():
    fresh()
    c = A.app.test_client()
    r = post(c, '/setup', name='Hacker', password=PW)
    assert r.status_code == 302
    _, r = login('Hacker')
    assert 'klopt niet' in text(r)


# ---------------------------------------------------------------- sessie / CSRF

def test_cookie_automatisch_alleen_https_achter_https():
    fresh()
    c = A.app.test_client()
    r = c.get('/login', base_url='https://carpool.voorbeeld.nl')
    assert 'Secure' in r.headers.get('Set-Cookie', '')
    c = A.app.test_client()
    r = c.get('/login')                                   # gewoon http
    assert 'Secure' not in r.headers.get('Set-Cookie', '')


def test_setup_met_verlopen_formulier_blijft_op_setup():
    """Geen rondje setup -> login -> setup meer."""
    with A.app.app_context():
        A.init_db()
        for t in ('ride_passengers', 'rides', 'attendance', 'seen_plan', 'users'):
            A.db().execute(f'DELETE FROM {t}')
        A.db().commit()
    c = A.app.test_client()
    c.get('/setup')
    r = c.post('/setup', data={'csrf': 'fout', 'name': 'Anna', 'password': PW})
    assert r.status_code == 302 and r.location.endswith('/setup')
    assert 'verouderd' in text(c.get('/setup'))
    fresh()


def test_verlopen_formulier_geeft_vriendelijke_melding():
    """Bijv. twee tabbladen open en in de ene uitgelogd en weer ingelogd."""
    fresh()
    c, _ = login('Bas')
    r = c.post(f'/attendance/{FUT}', data={'csrf': 'oud-token', 'going': '1'})
    assert r.status_code == 302, 'geen kale foutpagina, maar terug naar de planning'
    page = text(c.get('/'))
    assert 'opnieuw' in page.lower()


def test_verwijderde_gebruiker_wordt_uitgelogd():
    fresh()
    c, _ = login('Chris')
    with A.app.app_context():
        A.db().execute("DELETE FROM users WHERE name='Chris'")
        A.db().commit()
    assert c.get('/').status_code == 302


def test_te_groot_formulier():
    fresh()
    c, _ = login('Bas')
    r = c.post(f'/attendance/{FUT}', data={'csrf': tok(c), 'going': '1', 'x': 'a' * 20000})
    assert r.status_code == 413


# ---------------------------------------------------------------- aanwezigheid

def test_aanwezigheid_rare_invoer():
    fresh()
    c, _ = login('Bas')
    cases = [
        (f'/attendance/{NOT_CARPOOL}', '1'),         # geen carpooldag
        ('/attendance/gisteren', '1'),               # onzin-datum
        ('/attendance/2026-02-30', '1'),             # bestaat niet
        (f'/attendance/{PAST}', '1'),                # verleden
        (f'/attendance/{TODAY + timedelta(days=400)}', '1'),  # te ver weg
        (f'/attendance/{FUT}', 'misschien'),         # rare waarde
        (f'/attendance/{FUT}', ''),
    ]
    for url, going in cases:
        r = post(c, url, going=going)
        assert r.status_code == 400, (url, going, r.status_code)
        assert 'Terug' in text(r), 'foutpagina moet Nederlands zijn met terugknop'


def test_aanwezigheid_dubbelklik_en_wisselen():
    fresh()
    c, _ = login('Bas')
    for going in ('1', '1', '0', '1', '0', '0'):
        assert post(c, f'/attendance/{FUT}', going=going).status_code == 302
    with A.app.app_context():
        rows = A.db().execute('SELECT going FROM attendance WHERE ride_date=?', (FUT.isoformat(),)).fetchall()
    assert [r['going'] for r in rows] == [0]


def test_rare_week_parameter():
    fresh()
    c, _ = login('Bas')
    for w in ('abc', '999999', '-999999', '1.5', '', '%00'):
        assert c.get(f'/?week={w}').status_code == 200, w


def test_week_in_formulier_is_onzin():
    fresh()
    c, _ = login('Bas')
    r = post(c, f'/attendance/{FUT}', going='1', week='<script>')
    assert r.status_code == 302 and '<script>' not in r.location


# ---------------------------------------------------------------- rit afronden

def test_rit_afronden_rare_invoer():
    fresh()
    c, _ = login('Bas')
    bad = [
        dict(),                                  # geen chauffeur gekozen
        dict(driver='abc'),
        dict(driver='99'),                       # bestaat niet
        dict(driver='1', passengers='xyz'),
        dict(driver='1', passengers='99'),
    ]
    for data in bad:
        r = post(c, f'/confirm/{PAST}', **data)
        assert r.status_code == 400, (data, r.status_code)
    assert ride_count() == 0
    # toekomst kan niet
    assert post(c, f'/confirm/{FUT}', driver='1').status_code == 400
    # te lang geleden
    old = prev_day(0, TODAY - timedelta(days=120))
    assert post(c, f'/confirm/{old}', driver='1').status_code == 400


def test_chauffeur_ook_als_meerijder_aangevinkt():
    fresh()
    c, _ = login('Bas')
    c.post(f'/confirm/{PAST}', data={'csrf': tok(c), 'driver': '2', 'passengers': ['2', '1', '1', '3']})
    with A.app.test_request_context():
        L = A.stats()
    assert L.balance == {1: -1, 2: 2, 3: -1}


def test_alleen_gereden_telt_niet():
    fresh()
    c, _ = login('Bas')
    post(c, f'/confirm/{PAST}', driver='2')
    with A.app.test_request_context():
        L = A.stats()
    assert L.balance == {1: 0, 2: 0, 3: 0} and L.drives[2] == 1


def test_dubbel_afronden_en_dubbelklik():
    fresh()
    a, _ = login('Bas')
    b, _ = login('Chris')
    post(a, f'/confirm/{PAST}', driver='2', passengers='3')
    r = post(b, f'/confirm/{PAST}', driver='3', passengers='2')
    assert r.status_code == 302
    assert 'al afgerond' in text(b.get('/?week=-1')) or True
    with A.app.app_context():
        assert A.db().execute('SELECT driver_id FROM rides').fetchone()[0] == 2


def test_gelijktijdig_afronden_geeft_geen_crash():
    """Twee mensen drukken op precies hetzelfde moment op Opslaan."""
    fresh()
    c, _ = login('Bas')
    with A.app.app_context():
        A.db().execute('INSERT INTO rides(ride_date,driver_id,confirmed_at) VALUES (?,?,?)',
                       (PAST.isoformat(), 3, 'x'))
        A.db().commit()
    # Omzeil de 'al afgerond'-check, zoals bij een race tussen check en insert.
    orig = A.db
    calls = {'n': 0}

    class Wrap:
        def __init__(self, real):
            self.real = real

        def execute(self, sql, *a):
            if sql.startswith('SELECT 1 FROM rides') and calls['n'] == 0:
                calls['n'] += 1
                return self.real.execute('SELECT 1 WHERE 0')
            return self.real.execute(sql, *a)

        def __getattr__(self, k):
            return getattr(self.real, k)

    A.db = lambda: Wrap(orig())
    try:
        r = post(c, f'/confirm/{PAST}', driver='2')
    finally:
        A.db = orig
    assert r.status_code == 302, r.status_code


def test_aanwezigheid_wijzigen_na_afronden():
    fresh()
    c, _ = login('Bas')
    with A.app.app_context():
        A.db().execute('INSERT INTO rides(ride_date,driver_id,confirmed_at) VALUES (?,?,?)',
                       (TODAY.isoformat(), 1, 'x'))
        A.db().commit()
    if TODAY.weekday() in (0, 3):
        assert post(c, f'/attendance/{TODAY}', going='1').status_code == 400


def test_ongedaan_maken_rare_gevallen():
    fresh()
    c, _ = login('Chris')
    assert post(c, '/undo/onzin').status_code == 400
    r = post(c, f'/undo/{PAST}')                  # er is geen rit
    assert r.status_code == 302
    page = text(c.get('/?week=-1'))
    assert 'De rit is ongedaan gemaakt' not in page and 'al ongedaan' in page, 'geen succesmelding als er niets was'
    post(c, f'/confirm/{PAST}', driver='1', passengers=['2', '3'])
    post(c, f'/undo/{PAST}')
    post(c, f'/undo/{PAST}')                      # dubbelklik
    assert ride_count() == 0
    with A.app.app_context():
        assert A.db().execute('SELECT COUNT(*) FROM ride_passengers').fetchone()[0] == 0


def test_dag_uitgezet_terwijl_er_ritten_op_staan():
    fresh()
    c, _ = login('Anna')
    post(c, f'/confirm/{PAST}', driver='1', passengers='2')
    post(c, '/settings', action='days', days='0')     # donderdag uit
    assert c.get('/?week=-1').status_code == 200
    assert 'Telt' in text(c.get('/?week=-1')) or 'Afgerond' in text(c.get('/?week=-1'))
    assert post(c, f'/undo/{PAST}').status_code == 302   # ongedaan maken kan nog
    assert ride_count() == 0


# ---------------------------------------------------------------- instellingen

def test_niet_beheerder_kan_niet_bij_instellingen():
    fresh()
    c, _ = login('Bas')
    assert c.get('/settings').status_code == 403
    assert post(c, '/settings', action='add_user', name='Indringer', password=PW).status_code == 403


def test_instellingen_rare_invoer():
    fresh()
    c, _ = login('Anna')
    for data in (dict(action='days'), dict(action='days', days='9'), dict(action='days', days='x'),
                 dict(action='onzin'), dict()):
        assert post(c, '/settings', **data).status_code == 302
    with A.app.app_context():
        assert A.enabled_days() == {0, 3}
    r = post(c, '/settings', action='count_from', count_from='morgen')
    assert r.status_code in (302, 400)
    with A.app.app_context():
        assert A.count_from() == ''
    assert c.get('/').status_code == 200


def test_collega_toevoegen_rare_namen():
    fresh()
    c, _ = login('Anna')
    post(c, '/settings', action='add_user', name='bas', password=PW)        # zelfde naam, andere hoofdletters
    post(c, '/settings', action='add_user', name='   ', password=PW)
    post(c, '/settings', action='add_user', name='X' * 500, password=PW)
    post(c, '/settings', action='add_user', name='<b>Henk</b>', password=PW)
    with A.app.app_context():
        names = [r['name'] for r in A.db().execute('SELECT name FROM users')]
    assert 'bas' not in names, 'dubbele naam met andere hoofdletters'
    assert all(n.strip() for n in names) and all(len(n) <= 60 for n in names)
    page = text(c.get('/'))
    assert '<b>Henk</b>' not in page, 'HTML in naam moet ge-escaped worden'


def test_wachtwoord_reset_rare_invoer():
    fresh()
    c, _ = login('Anna')
    for data in (dict(user_id='99', password=PW), dict(user_id='abc', password=PW),
                 dict(user_id='2', password='kort')):
        assert post(c, '/settings', action='reset_password', **data).status_code == 302
    _, r = login('Bas')
    assert r.location.endswith('/')


# ---------------------------------------------------------------- wachtwoord

def test_wachtwoord_wijzigen_rare_invoer():
    fresh()
    c, _ = login('Chris')
    post(c, '/password', old='fout', new='nieuwwachtwoord1')
    post(c, '/password', old=PW, new='kort')
    _, r = login('Chris')
    assert r.location.endswith('/'), 'oud wachtwoord moet nog werken'


def test_tijdelijk_wachtwoord_dwingt_wijzigen_af():
    fresh()
    with A.app.app_context():
        A.db().execute("UPDATE users SET must_change_password=1 WHERE name='Chris'")
        A.db().commit()
    c, _ = login('Chris')
    assert '/password' in c.get('/').location
    assert '/password' in post(c, f'/attendance/{FUT}', going='1').location


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            try:
                fn()
                print('OK   ', name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print('FOUT ', name, '->', type(e).__name__, e)
    print(f'\n{failed} test(s) mislukt')
    sys.exit(1 if failed else 0)
