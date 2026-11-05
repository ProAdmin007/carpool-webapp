"""Tests voor het auditlog."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rare_acties import A, FUT, PAST, PW, fresh, login, post, text  # noqa: E402


def log():
    with A.app.app_context():
        return [dict(r) for r in A.db().execute('SELECT * FROM audit_log ORDER BY id')]


def actions():
    return [r['action'] for r in log()]


def reset_throttle():
    with A._fails_lock:
        A._fails.clear()


def test_inloggen_en_mislukte_pogingen_worden_vastgelegd():
    fresh(); reset_throttle()
    login('Bas', 'fout-wachtwoord')
    login('Niemand', 'x')
    c, _ = login('Bas')
    post(c, '/logout')
    rows = log()
    assert [r['action'] for r in rows] == ['login_failed', 'login_failed', 'login', 'logout']
    assert 'Naam: "Bas"' in rows[0]['detail'] and rows[0]['actor'] == ''
    assert '(bestaat niet)' in rows[1]['detail']
    assert rows[2]['actor'] == 'Bas' and rows[2]['ip']


def test_acties_rond_ritten_en_aanwezigheid():
    fresh(); reset_throttle()
    c, _ = login('Chris')
    post(c, f'/attendance/{FUT}', going='1')
    post(c, f'/confirm/{PAST}', driver='2', passengers=['1', '3'])
    post(c, f'/undo/{PAST}')
    post(c, f'/no-ride/{PAST}')
    rows = [r for r in log() if r['action'] != 'login']
    assert [r['action'] for r in rows] == ['attendance', 'ride_confirm', 'ride_undo', 'no_ride']
    assert rows[0]['detail'].endswith(': gaat')
    assert 'Bas reed, mee: Anna, Chris' in rows[1]['detail']
    assert 'chauffeur was Bas' in rows[2]['detail']
    assert all(r['actor'] == 'Chris' for r in rows)


def test_beheeracties_worden_vastgelegd():
    fresh(); reset_throttle()
    a, _ = login('Anna')
    post(a, '/settings', action='days', days=['0', '3', '4'])
    post(a, '/settings', action='count_from', count_from='2026-11-03')
    post(a, '/settings', action='add_user', name='Dirk', password=PW)
    post(a, '/settings', action='reset_password', user_id='2', password='tijdelijk-wachtwoord1')
    post(a, '/password', old=PW, new='nieuw-wachtwoord-99')
    post(a, '/agenda', action='renew')
    acts = actions()
    for expected in ('settings_days', 'settings_count_from', 'user_add', 'password_reset',
                     'password_change', 'calendar_renew'):
        assert expected in acts, expected
    days = next(r for r in log() if r['action'] == 'settings_days')
    assert days['detail'] == 'Maandag, Donderdag, Vrijdag'
    reset = next(r for r in log() if r['action'] == 'password_reset')
    assert reset['detail'] == 'Bas'
    assert not any('tijdelijk-wachtwoord1' in r['detail'] for r in log()), 'nooit wachtwoorden loggen'


def test_feedback_wordt_vastgelegd():
    fresh(); reset_throttle()
    b, _ = login('Bas')
    post(b, '/feedback', message='Mooie app!')
    a, _ = login('Anna')
    with A.app.app_context():
        fid = A.db().execute('SELECT id FROM feedback').fetchone()[0]
    post(a, '/settings', action='feedback_done', feedback_id=str(fid))
    assert 'feedback_sent' in actions() and 'feedback_done' in actions()


def test_alleen_beheerder_ziet_auditlog_en_filters_werken():
    fresh(); reset_throttle()
    b, _ = login('Bas')
    assert b.get('/audit').status_code == 403
    assert A.app.test_client().get('/audit').status_code == 302
    login('Chris', 'fout')
    a, _ = login('Anna')
    page = text(a.get('/audit'))
    assert 'Inloggen mislukt' in page and 'Niet ingelogd' in page
    assert 'Auditlog bekijken' in text(a.get('/settings'))
    only_bas = text(a.get('/audit?wie=2'))
    assert '<strong>Bas</strong>' in only_bas and '<strong>Anna</strong>' not in only_bas
    anon = text(a.get('/audit?wie=anoniem&soort=Inloggen'))
    assert 'Inloggen mislukt' in anon and '<strong>Bas</strong>' not in anon
    assert a.get('/audit?wie=abc&soort=<script>&voor=x').status_code == 200


def test_rare_tekens_in_log_worden_ge_escaped():
    fresh(); reset_throttle()
    login('<script>alert(1)</script>', 'x')
    a, _ = login('Anna')
    page = text(a.get('/audit'))
    assert '<script>alert(1)' not in page and '&lt;script&gt;' in page


def test_oude_regels_worden_opgeruimd_en_paginering():
    fresh(); reset_throttle()
    old = (datetime.now() - timedelta(days=400)).isoformat(timespec='seconds')
    with A.app.app_context():
        A.db().execute("INSERT INTO audit_log(at,actor,action) VALUES (?, 'x', 'login')", (old,))
        for i in range(150):
            A.db().execute("INSERT INTO audit_log(at,actor,action,detail) VALUES (?, 'Anna', 'attendance', ?)",
                           (datetime.now().isoformat(timespec='seconds'), f'regel {i}'))
        A.db().commit()
    a, _ = login('Anna')                       # nieuwe regel -> opruimen
    assert not any(r['at'] == old for r in log()), 'regel ouder dan een jaar is weg'
    page = text(a.get('/audit'))
    assert page.count('class="audit-row') == 100 and 'Oudere regels' in page
    import re
    nxt = re.search(r'href="(/audit\?[^"]*voor=\d+)"', page).group(1).replace('&amp;', '&')
    page2 = text(a.get(nxt))
    assert page2.count('class="audit-row') == 51 and 'Oudere regels' not in page2


def test_reset_via_server_wordt_vastgelegd():
    fresh()
    import subprocess
    import os
    env = dict(os.environ)
    r = subprocess.run([sys.executable, 'reset_wachtwoord.py', 'Bas'], cwd=str(Path(__file__).resolve().parent.parent),
                       env=env, capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stdout + r.stderr
    row = log()[-1]
    assert row['action'] == 'server_reset' and row['detail'] == 'Bas' and row['device'] == 'server'


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
