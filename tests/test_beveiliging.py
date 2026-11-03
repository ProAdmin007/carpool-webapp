"""Tests voor de beveiligingsmaatregelen."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rare_acties import A, FUT, PW, fresh, login, post, text, tok  # noqa: E402


def reset_throttle():
    with A._fails_lock:
        A._fails.clear()


def test_beveiligingsheaders():
    fresh()
    c, _ = login('Anna')
    r = c.get('/')
    h = r.headers
    assert h['X-Frame-Options'] == 'DENY'
    assert h['X-Content-Type-Options'] == 'nosniff'
    assert h['Cache-Control'] == 'no-store'
    csp = h['Content-Security-Policy']
    assert "frame-ancestors 'none'" in csp and "'unsafe-inline'" not in csp
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    scripts = re.findall(r'<script([^>]*)>', text(r))
    assert scripts and all(f'nonce="{nonce}"' in s for s in scripts), 'elk script heeft de juiste nonce'
    assert 'onsubmit=' not in text(r) and 'style="' not in text(r), 'geen inline handlers/styles'
    assert 'Strict-Transport-Security' in c.get('/', base_url='https://x.nl').headers
    assert 'Strict-Transport-Security' not in h


def test_nonce_is_per_verzoek_anders():
    fresh()
    c = A.app.test_client()
    a = c.get('/login').headers['Content-Security-Policy']
    b = c.get('/login').headers['Content-Security-Policy']
    assert a != b


def test_wachtwoord_raden_wordt_geblokkeerd():
    fresh(); reset_throttle()
    for _ in range(5):
        _, r = login('Anna', 'fout-wachtwoord')
        assert r.status_code == 200
    _, r = login('Anna', PW)                        # zelfs het goede wachtwoord even niet
    assert r.status_code == 429 and 'Te veel mislukte pogingen' in text(r)
    _, r = login('Bas', PW)                         # andere collega kan gewoon inloggen
    assert r.status_code == 302
    reset_throttle()
    _, r = login('Anna', PW)
    assert r.status_code == 302


def test_hoofdletters_omzeilen_blokkade_niet():
    fresh(); reset_throttle()
    for name in ('anna', 'ANNA', ' Anna ', 'aNNa', 'Anna'):
        login(name, 'fout')
    _, r = login('Anna', PW)
    assert r.status_code == 429
    reset_throttle()


def test_wachtwoord_wijzigen_logt_andere_apparaten_uit():
    fresh(); reset_throttle()
    telefoon, _ = login('Anna')
    laptop, _ = login('Anna')
    r = post(laptop, '/password', old=PW, new='nieuwwachtwoord99')
    assert r.status_code == 302
    assert laptop.get('/').status_code == 200, 'dit apparaat blijft ingelogd'
    assert telefoon.get('/').status_code == 302, 'ander apparaat is uitgelogd'


def test_reset_door_beheerder_logt_gebruiker_uit():
    fresh(); reset_throttle()
    bas, _ = login('Bas')
    admin, _ = login('Anna')
    post(admin, '/settings', action='reset_password', user_id='2', password='tijdelijk-wachtwoord1')
    assert bas.get('/').status_code == 302
    assert admin.get('/').status_code == 200


def test_bestaande_sessie_zonder_vingerafdruk_blijft_ingelogd():
    """Sessies van voor deze update mogen niet in één keer allemaal vervallen."""
    fresh()
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = 1
        s['csrf'] = 'x'
    assert c.get('/').status_code == 200
    assert c.get('/').status_code == 200


def test_vervalste_cookie_werkt_niet():
    fresh()
    c = A.app.test_client()
    c.set_cookie('session', 'eyJ1c2VyX2lkIjoxfQ.ZZZZZZ.vervalst')
    assert c.get('/').status_code == 302


def test_geen_open_redirect():
    fresh(); reset_throttle()
    c, _ = login('Anna')
    for week in ('//evil.example', 'https://evil.example', '\\\\evil.example'):
        r = post(c, f'/attendance/{FUT}', going='1', week=week)
        assert r.location.startswith('/') and not r.location.startswith('//'), r.location


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
