"""Web Push versturen (meldingen op telefoon en computer), zonder externe push-dienst.

Werkt met de standaard push-servers van de browsers (Google/Chrome, Mozilla, Apple, Microsoft).
- VAPID (RFC 8292): de server bewijst met een eigen sleutel wie de afzender is.
- Versleuteling (RFC 8291, 'aes128gcm'): alleen het apparaat van de ontvanger kan de melding lezen.
Alleen het pakket 'cryptography' is nodig; HTTP gaat via de standaardbibliotheek.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def b64u_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def raw_public(key) -> bytes:
    """Publieke sleutel als 65 bytes (0x04 || X || Y); werkt met een private of publieke sleutel."""
    if isinstance(key, ec.EllipticCurvePrivateKey):
        key = key.public_key()
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def load_vapid_key(path):
    """Eigen VAPID-sleutel; wordt de eerste keer aangemaakt en daarna altijd hergebruikt.

    Verandert deze sleutel, dan werken bestaande abonnementen niet meer: bewaar hem dus
    (in Docker staat hij op het /data-volume, net als secret_key.txt).
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        with open(path, 'wb') as f:
            f.write(pem)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    with open(path, 'rb') as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes = None, ephemeral=None) -> bytes:
    """Versleutel volgens RFC 8291 (Content-Encoding: aes128gcm). Geeft de complete body terug."""
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    if len(ua_public) != 65 or ua_public[0] != 4 or len(auth_secret) < 16:
        raise ValueError('ongeldige sleutels in het abonnement')
    ephemeral = ephemeral or ec.generate_private_key(ec.SECP256R1())
    as_public = raw_public(ephemeral)
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = ephemeral.exchange(ec.ECDH(), peer)
    # HKDF, uitgeschreven zodat elke stap overeenkomt met RFC 8291 §3.4
    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hmac(prk_key, b'WebPush: info\x00' + ua_public + as_public + b'\x01')
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b'Content-Encoding: aes128gcm\x00\x01')[:16]
    nonce = _hmac(prk, b'Content-Encoding: nonce\x00\x01')[:12]
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError('melding te lang')
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b'\x02', None)   # \x02 = laatste record
    header = salt + RECORD_SIZE.to_bytes(4, 'big') + bytes([len(as_public)]) + as_public
    return header + ciphertext


def vapid_header(endpoint: str, key, contact: str) -> str:
    """Authorization-header: een kort geldig, ondertekend JWT voor de push-server van dit abonnement."""
    parts = urlsplit(endpoint)
    claims = {'aud': f'{parts.scheme}://{parts.netloc}', 'exp': int(time.time()) + 12 * 3600, 'sub': contact}
    signing_input = (b64u(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode()) + '.'
                     + b64u(json.dumps(claims, separators=(',', ':')).encode()))
    r, s = decode_dss_signature(key.sign(signing_input.encode('ascii'), ec.ECDSA(hashes.SHA256())))
    token = signing_input + '.' + b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
    return f'vapid t={token}, k={b64u(raw_public(key))}'


def send(subscription: dict, message: dict, key, contact: str, ttl: int = 12 * 3600, timeout: float = 10):
    """Stuur één melding. Geeft de HTTP-status terug (201 = afgeleverd bij de push-server).

    404 of 410 betekent: dit abonnement bestaat niet meer en mag worden opgeruimd.
    """
    endpoint = subscription['endpoint']
    if urlsplit(endpoint).scheme != 'https':
        raise ValueError('push-endpoint moet https zijn')
    body = encrypt(json.dumps(message, ensure_ascii=False).encode('utf-8'), subscription['p256dh'],
                   subscription['auth'])
    req = urllib.request.Request(endpoint, data=body, method='POST', headers={  # nosec B310 - alleen https, gecontroleerd hierboven
        'Content-Type': 'application/octet-stream', 'Content-Encoding': 'aes128gcm',
        'TTL': str(ttl), 'Urgency': 'normal', 'Authorization': vapid_header(endpoint, key, contact)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
