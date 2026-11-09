"""Tests voor meldingen (Web Push): versleuteling, aanmelden en wanneer er een melding komt."""
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rare_acties import A, FUT, TODAY, fresh, login, post, text  # noqa: E402
from test_meldingen import FUT2, everyone_goes  # noqa: E402

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

push = A.push
KEYS = {}          # endpoint -> (private key, auth) zodat de test meldingen kan ontsleutelen
SENT = []          # (endpoint, bericht)


def decrypt(body, receiver, auth):
    """Ontvangerkant van RFC 8291, om te controleren wat de server verstuurt."""
    salt, idlen = body[:16], body[20]
    as_public, ct = body[21:21 + idlen], body[21 + idlen:]
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    ecdh = receiver.exchange(ec.ECDH(), peer)
    ua_public = push.raw_public(receiver)
    ikm = push._hmac(push._hmac(auth, ecdh), b'WebPush: info\x00' + ua_public + as_public + b'\x01')
    prk = push._hmac(salt, ikm)
    cek = push._hmac(prk, b'Content-Encoding: aes128gcm\x00\x01')[:16]
    nonce = push._hmac(prk, b'Content-Encoding: nonce\x00\x01')[:12]
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert plain.endswith(b'\x02')
    return plain[:-1]


def fake_send(subscription, message, key, contact, **kw):
    # Echt versleutelen en weer ontsleutelen, zodat de hele keten getest wordt (behalve het netwerk).
    receiver, auth = KEYS[subscription['endpoint']]
    body = push.encrypt(json.dumps(message).encode(), subscription['p256dh'], subscription['auth'])
    SENT.append((subscription['endpoint'], json.loads(decrypt(body, receiver, auth))))
    assert push.vapid_header(subscription['endpoint'], key, contact).startswith('vapid t=')
    return 201


REAL_SEND = push.send
A.push.send = fake_send


def new_sub(n):
    receiver, auth = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
    endpoint = f'https://fcm.googleapis.com/fcm/send/test-{n}-{os.urandom(4).hex()}'
    KEYS[endpoint] = (receiver, auth)
    return {'endpoint': endpoint, 'keys': {'p256dh': push.b64u(push.raw_public(receiver)), 'auth': push.b64u(auth)}}


def subscribe(name):
    c, _ = login(name)
    sub = new_sub(name)
    r = post(c, '/meldingen/aan', subscription=json.dumps(sub))
    assert r.status_code == 200 and r.get_json()['ok'], r.get_data(as_text=True)
    return c, sub['endpoint']


def run(now, actors=()):
    SENT.clear()
    with A.app.app_context():
        for uid, msg in A.collect_notifications(now, set(actors)):
            A.deliver(uid, msg)
    return list(SENT)


def titles_for(sent, endpoint):
    return [m['title'] for e, m in sent if e == endpoint]


def at(hour, minute=0, day=None):
    return datetime.combine(day or TODAY, time(hour, minute))


def free_days():
    """Dagen binnen de meldingshorizon die geen vaste carpooldag zijn (onafhankelijk van vandaag)."""
    return [TODAY + timedelta(days=i) for i in range(1, 8) if (TODAY + timedelta(days=i)).weekday() not in (0, 3)]


def all_days_active():
    with A.app.app_context():
        A.db().execute("UPDATE settings SET value='0,1,2,3,4,5,6' WHERE key='weekdays'")
        A.db().commit()


def test_versleuteling_en_vapid():
    receiver, auth = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
    body = push.encrypt('Morgen rijd jij 🚗'.encode(), push.b64u(push.raw_public(receiver)), push.b64u(auth))
    assert decrypt(body, receiver, auth).decode() == 'Morgen rijd jij 🚗'
    header = push.vapid_header('https://fcm.googleapis.com/fcm/send/x', A.vapid_key(), 'https://example.org')
    token = header.split('t=')[1].split(',')[0]
    claims = json.loads(push.b64u_decode(token.split('.')[1]))
    assert claims['aud'] == 'https://fcm.googleapis.com' and claims['sub'] == 'https://example.org'


def test_versturen_controleert_certificaat_via_systeem():
    # Apple's push-server faalde met Python's eigen certificaatlijst op Windows; send() moet
    # de truststore-context (certificaatopslag van het systeem) gebruiken.
    import truststore
    import urllib.request
    seen = {}

    class Resp:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout, context):
        seen.update(ctx=context, url=req.full_url, headers=dict(req.header_items()))
        return Resp()

    real = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        receiver = ec.generate_private_key(ec.SECP256R1())
        sub = {'endpoint': 'https://web.push.apple.com/abc', 'p256dh': push.b64u(push.raw_public(receiver)),
               'auth': push.b64u(os.urandom(16))}
        assert REAL_SEND(sub, {'title': 'x'}, A.vapid_key(), A.PUSH_CONTACT) == 201
    finally:
        urllib.request.urlopen = real
    assert isinstance(seen['ctx'], truststore.SSLContext) and seen['url'] == sub['endpoint']
    assert seen['headers']['Authorization'].startswith('vapid t=') and seen['headers']['Ttl']


def test_alleen_echte_push_diensten():
    ok = ['https://fcm.googleapis.com/fcm/send/a', 'https://updates.push.services.mozilla.com/wpush/v2/a',
          'https://web.push.apple.com/a', 'https://wns2-db5p.notify.windows.com/w/?token=a']
    bad = ['http://fcm.googleapis.com/a', 'https://evil.example/a', 'https://evilgoogleapis.com/a',
           'https://notify.windows.com.evil.com/a', 'https://127.0.0.1/a', '']
    assert all(A.valid_push_endpoint(u) for u in ok)
    assert not any(A.valid_push_endpoint(u) for u in bad)


def test_aanmelden_en_controles():
    fresh()
    c, endpoint = subscribe('Bas')
    other = new_sub('x')
    again = {'endpoint': endpoint, 'keys': other['keys']}       # zelfde apparaat, nieuwe sleutels
    KEYS[endpoint] = KEYS[other['endpoint']]
    assert post(c, '/meldingen/aan', subscription=json.dumps(again)).status_code == 200
    with A.app.app_context():
        assert A.db().execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0] == 1, 'zelfde apparaat niet dubbel'
    bad = new_sub('y'); bad['endpoint'] = 'https://evil.example/x'
    assert post(c, '/meldingen/aan', subscription=json.dumps(bad)).status_code == 400
    assert post(c, '/meldingen/aan', subscription='geen json').status_code == 400
    assert post(c, '/meldingen/aan', subscription=json.dumps({'endpoint': endpoint, 'keys': {'p256dh': 'abc', 'auth': 'x'}})).status_code == 400
    assert A.app.test_client().post('/meldingen/aan').status_code in (302, 400)
    page = text(c.get('/meldingen'))
    assert 'Meldingen aanzetten' in page and 'data-key="B' in page
    assert post(c, '/meldingen/test').get_json()['ok']
    assert SENT[-1][1]['title'] == '🔔 Het werkt!'
    post(c, '/meldingen/uit', endpoint=endpoint)
    with A.app.app_context():
        assert A.db().execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0] == 0


def test_planning_gewijzigd_alleen_voor_wie_het_raakt():
    fresh()
    everyone_goes(FUT, FUT2)
    anna, ea = subscribe('Anna')
    _, eb = subscribe('Bas')
    _, ec_ = subscribe('Chris')
    assert run(at(12)) == [], 'na aanmelden geen stapel oude meldingen'
    with A.app.app_context():
        before = next(d for d in A.plan_for(FUT) if d['iso'] == FUT.isoformat())['driver']
    assert before == 1   # Anna rijdt de eerste dag
    post(anna, f'/attendance/{FUT}', going='0')
    sent = run(at(12), actors={1})
    with A.app.app_context():
        after = next(d for d in A.plan_for(FUT) if d['iso'] == FUT.isoformat())['driver']
    new_ep = {2: eb, 3: ec_}[after]
    msgs = [m for e, m in sent if e == new_ep]
    assert msgs and 'jij rijdt nu (eerst Anna)' in msgs[0]['body']
    assert not [m for e, m in sent if e == ea], 'Anna paste het zelf aan: geen melding'
    assert run(at(12)) == [], 'geen dubbele melding'


def test_de_avond_ervoor():
    fresh()
    all_days_active()
    tomorrow = TODAY + timedelta(days=1)
    _, ea = subscribe('Anna')
    _, eb = subscribe('Bas')
    _, ec_ = subscribe('Chris')
    for name in ('Anna', 'Bas'):
        c, _ = login(name)
        post(c, f'/attendance/{tomorrow}', going='1')
    run(at(12))                                     # planningswijzigingen van het aanmelden wegwerken
    assert not [m for e, m in run(at(18)) if m['title'].startswith('🚗 Morgen')], 'pas vanaf 19:00'
    sent = run(at(19, 30))
    assert titles_for(sent, ea) == ['🚗 Morgen rijd jij']
    assert titles_for(sent, eb) == ['🚗 Morgen rijdt Anna']
    assert titles_for(sent, ec_) == ['Ga je morgen naar kantoor?']
    assert run(at(20)) == [], 'maar één keer per avond'


def test_na_de_rit_vragen_wie_er_reed():
    fresh()
    all_days_active()
    _, ea = subscribe('Anna')
    _, eb = subscribe('Bas')
    for name in ('Anna', 'Bas'):
        c, _ = login(name)
        post(c, f'/attendance/{TODAY}', going='1')
    run(at(12))
    assert not [1 for e, m in run(at(17)) if m['title'] == 'Wie reed er vandaag?']
    sent = run(at(18))
    assert titles_for(sent, ea).count('Wie reed er vandaag?') == 1 and 'Wie reed er vandaag?' in titles_for(sent, eb)
    assert not [1 for e, m in run(at(19)) if m['title'] == 'Wie reed er vandaag?']


def test_uitnodiging_extra_dag():
    fresh()
    fri = free_days()[0]
    _, ea = subscribe('Anna')
    _, eb = subscribe('Bas')
    bas, _ = login('Bas')
    post(bas, '/extra-day', day=fri.isoformat())
    sent = run(at(12), actors={2})
    assert any('Bas wil ook op' in m['title'] for e, m in sent if e == ea)
    assert not [m for e, m in sent if e == eb]
    assert not [m for e, m in run(at(12)) if 'wil ook op' in m['title']]


def test_aangemeld_voor_extra_dag():
    fresh()
    sat = free_days()[1]
    _, ea = subscribe('Anna')
    _, ec_ = subscribe('Chris')
    bas, _ = login('Bas')
    post(bas, '/extra-day', day=sat.isoformat(), **{'with': ['1']})
    sent = run(at(12), actors={2})
    titles = titles_for(sent, ea)
    assert len(titles) == 1 and titles[0].startswith('＋ Bas heeft je aangemeld voor '), titles
    assert any('Kun je niet?' in m['body'] for e, m in sent if e == ea)
    assert any('Bas wil ook op' in m['title'] for e, m in sent if e == ec_)


def test_verlopen_abonnement_wordt_opgeruimd():
    fresh()
    subscribe('Bas')
    A.push.send = lambda *a, **k: 410
    try:
        with A.app.app_context():
            assert A.deliver(2, {'title': 'x'}) == 0
            assert A.db().execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0] == 0
    finally:
        A.push.send = fake_send


def test_service_worker_en_toegang():
    fresh()
    r = A.app.test_client().get('/sw.js')
    assert r.status_code == 200 and 'javascript' in r.headers['Content-Type']
    assert r.headers['Service-Worker-Allowed'] == '/' and 'showNotification' in text(r)
    assert A.app.test_client().get('/meldingen').status_code == 302


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
