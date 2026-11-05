"""Tests voor feedback en de agenda-snelknop."""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rare_acties import A, TODAY, fresh, login, post, text  # noqa: E402
from test_meldingen import everyone_goes  # noqa: E402


def count_feedback():
    with A.app.app_context():
        return A.db().execute('SELECT COUNT(*) FROM feedback').fetchone()[0]


def test_feedback_versturen_en_beheerder_ziet_het():
    fresh()
    bas, _ = login('Bas')
    assert 'Wat kan beter?' in text(bas.get('/feedback'))
    r = post(bas, '/feedback', message='Graag ook <b>dinsdag</b> kunnen kiezen!')
    assert r.status_code == 302
    assert 'Bedankt voor je feedback' in text(bas.get('/'))
    anna, _ = login('Anna')                                  # beheerder
    page = text(anna.get('/'))
    assert 'class="badge"' in page and '#feedback' in page, 'teller in het menu'
    settings = text(anna.get('/settings'))
    assert 'Graag ook &lt;b&gt;dinsdag&lt;/b&gt; kunnen kiezen!' in settings, 'ge-escaped en zichtbaar'
    assert '<strong>Bas</strong>' in settings


def test_afhandelen_haalt_teller_weg():
    fresh()
    bas, _ = login('Bas')
    post(bas, '/feedback', message='Top app')
    anna, _ = login('Anna')
    with A.app.app_context():
        fid = A.db().execute('SELECT id FROM feedback').fetchone()[0]
    post(anna, '/settings', action='feedback_done', feedback_id=str(fid))
    assert 'class="badge"' not in text(anna.get('/'))
    post(anna, '/settings', action='feedback_open', feedback_id=str(fid))
    assert 'class="badge"' in text(anna.get('/'))


def test_feedback_rare_invoer():
    fresh()
    bas, _ = login('Bas')
    post(bas, '/feedback', message='  ')
    post(bas, '/feedback', message='x' * 2001)
    assert count_feedback() == 0
    for i in range(12):
        post(bas, '/feedback', message=f'bericht {i}')
    assert count_feedback() == 10, 'maximaal 10 per dag'


def test_alleen_beheerder_kan_afhandelen_en_zien():
    fresh()
    bas, _ = login('Bas')
    post(bas, '/feedback', message='geheim voor beheerder')
    assert 'class="badge"' not in text(bas.get('/'))
    assert post(bas, '/settings', action='feedback_done', feedback_id='1').status_code == 403
    assert A.app.test_client().get('/feedback').status_code == 302, 'inloggen vereist'


def test_agendaknop_bovenaan_en_feedbacklink():
    fresh()
    everyone_goes(*[TODAY + timedelta(days=i) for i in range(1, 15) if (TODAY + timedelta(days=i)).weekday() in (0, 3)])
    c, _ = login('Chris')
    page = text(c.get('/'))
    top = page[:page.index('class="toolbar"')]
    assert 'In je agenda' in top, 'agendaknop staat bovenaan bij Daarna'
    assert page.count('href="/feedback"') == 1, 'op de planning alleen de feedbackkaart'
    assert 'href="/feedback"' in text(c.get('/agenda')), 'op andere pagina\'s de link onderaan'


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
