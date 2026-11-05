"""Kleine carpoolplanner voor een vertrouwde groep collega's."""
import hashlib
import mimetypes
import os
import secrets
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import (Flask, abort, flash, g, has_request_context, redirect, render_template, request,
                   send_from_directory, session, url_for)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
mimetypes.add_type('application/manifest+json', '.webmanifest')
mimetypes.add_type('image/svg+xml', '.svg')
DB_PATH = Path(os.environ.get('CARPOOL_DB', str(ROOT / 'carpool.sqlite3')))
app = Flask(__name__)


def load_secret_key():
    """Vaste sleutel, zodat iedereen ook na een herstart ingelogd blijft."""
    if os.environ.get('SECRET_KEY'):
        return os.environ['SECRET_KEY']
    key_file = Path(os.environ.get('SECRET_KEY_FILE', str(ROOT / 'secret_key.txt')))
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32), encoding='utf-8')
        try:
            key_file.chmod(0o600)  # alleen leesbaar voor de app zelf (Linux/Docker)
        except OSError:
            pass
    return key_file.read_text(encoding='utf-8').strip()


app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', 'auto') == '1',
    # Praktisch 'voor altijd' ingelogd; de cookie wordt bij elk bezoek weer verlengd.
    PERMANENT_SESSION_LIFETIME=timedelta(days=3650),
    SESSION_REFRESH_EACH_REQUEST=True,
    MAX_CONTENT_LENGTH=16 * 1024,
)
WEEKDAYS = ['Maandag', 'Dinsdag', 'Woensdag', 'Donderdag', 'Vrijdag', 'Zaterdag', 'Zondag']
MONTHS = ['', 'januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december']


def db():
    if 'db' not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA journal_mode=WAL')
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def init_db():
    db().executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          is_admin INTEGER NOT NULL DEFAULT 0,
          must_change_password INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS attendance (
          user_id INTEGER NOT NULL REFERENCES users(id),
          ride_date TEXT NOT NULL,
          going INTEGER NOT NULL CHECK(going IN (0,1)),
          PRIMARY KEY (user_id, ride_date)
        );
        CREATE TABLE IF NOT EXISTS rides (
          ride_date TEXT PRIMARY KEY,
          driver_id INTEGER NOT NULL REFERENCES users(id),
          confirmed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ride_passengers (
          ride_date TEXT NOT NULL REFERENCES rides(ride_date) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id),
          PRIMARY KEY (ride_date, user_id)
        );
        CREATE TABLE IF NOT EXISTS seen_plan (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          ride_date TEXT NOT NULL,
          driver_id INTEGER,
          shown_at TEXT,
          PRIMARY KEY (user_id, ride_date)
        );
        CREATE TABLE IF NOT EXISTS feedback (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL,
          message TEXT NOT NULL,
          done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          at TEXT NOT NULL,
          user_id INTEGER,
          actor TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL,
          detail TEXT NOT NULL DEFAULT '',
          ip TEXT NOT NULL DEFAULT '',
          device TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log(at);
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO settings(key, value) VALUES ('weekdays', '0,3');
    ''')
    cols = {r['name'] for r in db().execute('PRAGMA table_info(seen_plan)')}
    if 'shown_at' not in cols:  # bestaande database bijwerken
        db().execute('ALTER TABLE seen_plan ADD COLUMN shown_at TEXT')
    if 'cal_token' not in {r['name'] for r in db().execute('PRAGMA table_info(users)')}:
        db().execute('ALTER TABLE users ADD COLUMN cal_token TEXT')
        db().execute('CREATE UNIQUE INDEX IF NOT EXISTS users_cal_token ON users(cal_token)')
    db().commit()


# COOKIE_SECURE: 'auto' (standaard) = cookie alleen-https als de pagina via https is geopend
# (achter de reverse proxy), anders gewone cookie zodat inloggen via http ook werkt.
# '1' = altijd alleen-https, '0' = nooit.
COOKIE_MODE = os.environ.get('COOKIE_SECURE', 'auto')


class AutoSecureSessionInterface(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):
        if COOKIE_MODE == 'auto':
            return request.is_secure
        return super().get_cookie_secure(app)


app.session_interface = AutoSecureSessionInterface()


def password_version(user):
    """Vingerafdruk van het wachtwoord: verandert het wachtwoord, dan vervallen oude sessies."""
    return hashlib.sha256(user['password_hash'].encode()).hexdigest()[:16]


def start_session(user):
    session.clear()
    session['user_id'] = user['id']
    session['pv'] = password_version(user)
    session.permanent = True


# --- Bescherming tegen wachtwoorden raden -----------------------------------------
MAX_FAILS_PER_NAME = 5      # per naam ...
MAX_FAILS_PER_IP = 30       # ... en per IP-adres
FAIL_WINDOW = 15 * 60       # binnen 15 minuten
_fails = {}
_fails_lock = threading.Lock()
DUMMY_HASH = generate_password_hash(secrets.token_hex(16))  # gelijke rekentijd bij onbekende naam


def _recent(key, now):
    stamps = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW]
    _fails[key] = stamps
    return stamps


def login_blocked(name, ip):
    now = time.monotonic()
    with _fails_lock:
        return (len(_recent('n:' + name.lower(), now)) >= MAX_FAILS_PER_NAME
                or len(_recent('ip:' + ip, now)) >= MAX_FAILS_PER_IP)


def register_fail(name, ip):
    now = time.monotonic()
    with _fails_lock:
        for key in ('n:' + name.lower(), 'ip:' + ip):
            _recent(key, now).append(now)


def clear_fails(name):
    with _fails_lock:
        _fails.pop('n:' + name.lower(), None)


@app.before_request
def before():
    init_db()
    g.csp_nonce = secrets.token_urlsafe(16)
    g.user = None
    if session.get('user_id'):
        g.user = db().execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
        if g.user and 'pv' not in session:
            session['pv'] = password_version(g.user)   # bestaande sessies van voor deze controle
        if not g.user or session.get('pv') != password_version(g.user):
            session.clear()                           # account weg of wachtwoord gewijzigd
            g.user = None
        else:
            session.permanent = True
    if request.method == 'POST':
        if not session.get('csrf') or not secrets.compare_digest(request.form.get('csrf', ''), session['csrf']):
            # Meestal een oud tabblad of een pagina die lang open stond: gewoon opnieuw laten proberen.
            if app.config['SESSION_COOKIE_SECURE'] and not request.is_secure:
                abort(400, 'Deze app is ingesteld op alleen https (COOKIE_SECURE=1), maar je opent hem via '
                           'http. Open de app via het https-adres, of verwijder COOKIE_SECURE.')
            flash('Deze pagina was verouderd. Probeer het opnieuw.', 'error')
            if request.endpoint in ('login', 'setup'):
                return redirect(request.path)  # zelfde pagina, met melding
            return redirect(url_for('index') if g.user else url_for('login'))
    if g.user and g.user['must_change_password'] and request.endpoint not in ('change_password', 'logout', 'static'):
        return redirect(url_for('change_password'))


@app.context_processor
def shared():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    insecure = app.config['SESSION_COOKIE_SECURE'] and not request.is_secure
    user = g.get('user')
    open_feedback = 0
    if user and user['is_admin']:
        open_feedback = db().execute('SELECT COUNT(*) FROM feedback WHERE done=0').fetchone()[0]
    return {'csrf': session['csrf'], 'me': user, 'weekdays': WEEKDAYS, 'https_needed': insecure,
            'csp_nonce': g.get('csp_nonce', ''), 'open_feedback': open_feedback}


@app.after_request
def security_headers(resp):
    nonce = g.get('csp_nonce', '')
    resp.headers.setdefault('Content-Security-Policy',
                            "default-src 'self'; script-src 'self' 'nonce-%s'; style-src 'self'; "
                            "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; "
                            "base-uri 'none'; object-src 'none'" % nonce)
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'same-origin')
    resp.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    if request.is_secure:
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    if request.endpoint != 'static':
        resp.headers['Cache-Control'] = 'no-store'  # persoonlijke planning niet in caches bewaren
    return resp


def login_required(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        if not g.user:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return inner


def admin_required(fn):
    @wraps(fn)
    @login_required
    def inner(*args, **kwargs):
        if not g.user['is_admin']:
            abort(403)
        return fn(*args, **kwargs)
    return inner


def all_users():
    return db().execute('SELECT id, name FROM users ORDER BY id').fetchall()


def count_from():
    """Ritten vóór deze datum tellen niet mee in het saldo (leeg = alles telt)."""
    row = db().execute("SELECT value FROM settings WHERE key='count_from'").fetchone()
    return row['value'] if row else ''


def enabled_days():
    row = db().execute("SELECT value FROM settings WHERE key='weekdays'").fetchone()
    return set(int(x) for x in row['value'].split(','))


def fmt(d):
    return f'{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month]}'


def monday(d):
    return d - timedelta(days=d.weekday())


def short(d):
    return f'{d.day} {MONTHS[d.month][:3]}'


def ride_history():
    """Alle bevestigde ritten: {datum: {'driver': id, 'passengers': [ids]}}."""
    rides = {r['ride_date']: {'driver': r['driver_id'], 'passengers': []}
             for r in db().execute('SELECT ride_date, driver_id FROM rides')}
    for p in db().execute('SELECT ride_date, user_id FROM ride_passengers ORDER BY user_id'):
        if p['ride_date'] in rides:
            rides[p['ride_date']]['passengers'].append(p['user_id'])
    return rides


class Ledger:
    """Houdt per persoon een saldo bij: rijden levert +1 per meerijder op, meerijden kost 1.

    Er wordt ook per tweetal bijgehouden wie wie heeft gereden (`owed`). Bij het kiezen tellen
    alleen de verhoudingen met de collega's die die dag gaan: zo betaalt niemand de schuld van
    een afwezige collega af. Als iedereen er is, komt dit precies overeen met het totale saldo.
    """

    def __init__(self, users):
        self.balance = {u['id']: 0 for u in users}
        self.drives = {u['id']: 0 for u in users}
        self.rides_along = {u['id']: 0 for u in users}
        self.last = {u['id']: '0000-00-00' for u in users}
        self.owed = defaultdict(int)   # (a, b): keer dat a b reed, min keer dat b a reed

    def add(self, iso, driver, passengers):
        passengers = [p for p in passengers if p != driver and p in self.balance]
        if driver in self.balance:
            self.balance[driver] += len(passengers)
            self.drives[driver] += 1
            self.last[driver] = iso
        for p in passengers:
            self.balance[p] -= 1
            self.rides_along[p] += 1
            if driver in self.balance:
                self.owed[(driver, p)] += 1
                self.owed[(p, driver)] -= 1

    def score(self, uid, present_ids):
        """Saldo van `uid` alleen ten opzichte van de collega's die er die dag zijn."""
        return sum(self.owed[(uid, v)] for v in present_ids if v != uid)

    def pick(self, present):
        # Laagste saldo t.o.v. de aanwezigen eerst; bij gelijkstand wie het langst niet reed,
        # daarna minst gereden.
        ids = [u['id'] for u in present]
        return min(present, key=lambda u: (self.score(u['id'], ids), self.last[u['id']],
                                           self.drives[u['id']], u['id']))['id']


def plan_for(end):
    """Plan alle carpooldagen t/m `end`.

    Bevestigde ritten zijn definitief en tellen mee in het saldo. Vandaag en toekomstige
    dagen worden virtueel ingepland (met alle aanwezigen als meerijders), zodat niet
    telkens dezelfde persoon wordt gekozen. Verleden dagen zonder bevestiging tellen niet mee.
    """
    users = all_users()
    by_id = {u['id']: u for u in users}
    rides = ride_history()
    attendance = defaultdict(dict)
    for a in db().execute('SELECT user_id, ride_date, going FROM attendance'):
        attendance[a['ride_date']][a['user_id']] = a['going']
    ledger = Ledger(users)
    since = count_from()
    today = date.today()
    active = enabled_days()
    first = min([date.fromisoformat(d) for d in rides] + [monday(today) - timedelta(weeks=1)])
    days = []
    d = first
    while d <= end:
        iso = d.isoformat()
        ride = rides.get(iso)
        if ride or d.weekday() in active:
            present = [u for u in users if attendance[iso].get(u['id']) == 1]
            info = {'date': d, 'iso': iso, 'label': fmt(d), 'present': present,
                    'responses': attendance[iso], 'done': ride is not None,
                    'past': d < today, 'today': d == today,
                    'can_confirm': d <= today and not ride}
            if ride:
                info['driver'] = ride['driver']
                info['passengers'] = [by_id[p] for p in ride['passengers'] if p in by_id]
                if iso >= since:
                    ledger.add(iso, ride['driver'], ride['passengers'])
                elif ride['driver'] in ledger.last:
                    # Telt niet mee voor het saldo, maar wel voor 'wie reed het langst geleden'.
                    ledger.last[ride['driver']] = iso
            else:
                # Gaat er maar één persoon, dan is er geen carpool: geen chauffeur, telt niet mee.
                info['solo'] = len(present) == 1
                info['driver'] = ledger.pick(present) if len(present) > 1 else None
                info['passengers'] = [u for u in present if u['id'] != info['driver']] if info['driver'] else []
                if info['driver'] is not None and d >= today:
                    ledger.add(iso, info['driver'], [u['id'] for u in info['passengers']])
            days.append(info)
        d += timedelta(days=1)
    return days


def stats():
    ledger = Ledger(all_users())
    since = count_from()
    for iso, ride in sorted(ride_history().items()):
        if iso >= since:
            ledger.add(iso, ride['driver'], ride['passengers'])
    return ledger


def clean_name(raw):
    return ' '.join(raw.split())  # spaties aan begin/eind en dubbele spaties weg


def find_user(name):
    return db().execute('SELECT * FROM users WHERE name=? COLLATE NOCASE', (clean_name(name),)).fetchone()


# --- Auditlog ---------------------------------------------------------------------
AUDIT_KEEP_DAYS = 365
AUDIT_ACTIONS = {   # actie: (categorie, omschrijving)
    'login': ('Inloggen', 'Ingelogd'),
    'login_failed': ('Inloggen', 'Inloggen mislukt'),
    'login_blocked': ('Inloggen', 'Geblokkeerd na te veel pogingen'),
    'logout': ('Inloggen', 'Uitgelogd'),
    'setup': ('Account', 'Beheerder aangemaakt'),
    'password_change': ('Account', 'Eigen wachtwoord gewijzigd'),
    'calendar_renew': ('Account', 'Nieuwe agendalink gemaakt'),
    'server_reset': ('Account', 'Wachtwoord gereset via de server'),
    'attendance': ('Aanwezigheid', 'Aanwezigheid doorgegeven'),
    'ride_confirm': ('Ritten', 'Rit afgerond'),
    'ride_undo': ('Ritten', 'Rit ongedaan gemaakt'),
    'no_ride': ('Ritten', 'Niet gereden'),
    'settings_days': ('Instellingen', 'Carpooldagen gewijzigd'),
    'settings_count_from': ('Instellingen', 'Startdatum telling gewijzigd'),
    'user_add': ('Instellingen', 'Collega toegevoegd'),
    'password_reset': ('Instellingen', 'Wachtwoord van collega gereset'),
    'feedback_sent': ('Feedback', 'Feedback verstuurd'),
    'feedback_done': ('Feedback', 'Feedback afgehandeld'),
    'feedback_open': ('Feedback', 'Feedback heropend'),
}
AUDIT_CATEGORIES = sorted({c for c, _ in AUDIT_ACTIONS.values()})


def client_device():
    ua = request.headers.get('User-Agent', '').lower()
    for key, label in (('iphone', 'iPhone'), ('ipad', 'iPad'), ('android', 'Android'), ('windows', 'Windows'),
                       ('mac os', 'Mac'), ('cros', 'Chromebook'), ('linux', 'Linux')):
        if key in ua:
            return label
    return 'onbekend'


def audit(action, detail='', user=None):
    """Leg een actie vast in het auditlog (en ruim regels ouder dan een jaar op)."""
    if user is None and has_request_context():
        user = g.get('user')
    if has_request_context():
        # Achter Cloudflare staat het echte IP-adres in CF-Connecting-IP; anders het adres van de verbinding.
        ip = request.headers.get('CF-Connecting-IP') or request.remote_addr or ''
        device = client_device()
    else:
        ip, device = '', 'server'
    now = datetime.now()
    db().execute('INSERT INTO audit_log(at,user_id,actor,action,detail,ip,device) VALUES (?,?,?,?,?,?,?)',
                 (now.isoformat(timespec='seconds'), user['id'] if user else None, user['name'] if user else '',
                  action, detail[:500], ip[:64], device))
    db().execute('DELETE FROM audit_log WHERE at<?',
                 ((now - timedelta(days=AUDIT_KEEP_DAYS)).isoformat(timespec='seconds'),))
    db().commit()


ERRORS = {400: 'Dat ging niet goed', 403: 'Dit mag je niet doen', 404: 'Pagina niet gevonden',
          413: 'Dat formulier was te groot', 500: 'Er ging iets mis'}


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(500)
def error_page(e):
    code = getattr(e, 'code', 500)
    detail = getattr(e, 'description', '') if code in (400, 403) else ''
    if detail and detail.startswith(('The browser', 'You don')):  # standaard Engelse teksten
        detail = ''
    return render_template('error.html', title=ERRORS.get(code, 'Er ging iets mis'), detail=detail), code


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon',
                               max_age=7 * 24 * 3600)


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if db().execute('SELECT COUNT(*) FROM users').fetchone()[0]:
        return redirect(url_for('login'))
    if request.method == 'POST':
        name = clean_name(request.form.get('name', ''))
        pw = request.form.get('password', '')
        if not 2 <= len(name) <= 60 or len(pw) < 12:
            flash('Gebruik een naam van 2 tot 60 tekens en een wachtwoord van minimaal 12 tekens.', 'error')
        else:
            db().execute('INSERT INTO users(name, password_hash, is_admin) VALUES (?, ?, 1)',
                         (name, generate_password_hash(pw)))
            db().commit()
            audit('setup', name, user=find_user(name))
            flash('Beheerder aangemaakt. Log nu in.', 'success')
            return redirect(url_for('login'))
    return render_template('setup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not db().execute('SELECT COUNT(*) FROM users').fetchone()[0]:
        return redirect(url_for('setup'))
    if g.user:
        return redirect(url_for('index'))
    if request.method == 'POST':
        pw = request.form.get('password', '')
        name = clean_name(request.form.get('name', ''))
        ip = request.remote_addr or '?'
        if login_blocked(name, ip):
            audit('login_blocked', f'Naam: "{name[:60]}"')
            flash('Te veel mislukte pogingen. Wacht een kwartier en probeer het dan opnieuw.', 'error')
            return render_template('login.html'), 429
        user = find_user(name)  # hoofdletters en spaties maken niet uit
        ok = check_password_hash(user['password_hash'] if user else DUMMY_HASH, pw)
        if user and ok:
            clear_fails(name)
            start_session(user)
            audit('login', user=user)
            return redirect(url_for('index'))
        register_fail(name, ip)
        audit('login_failed', f'Naam: "{name[:60]}"' + ('' if user else ' (bestaat niet)'))
        flash('Naam of wachtwoord klopt niet.', 'error')
    return render_template('login.html')


@app.post('/logout')
def logout():
    if g.user:
        audit('logout')
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    today = date.today()
    week_offset = request.args.get('week', '0')
    try:
        offset = max(-52, min(52, int(week_offset)))
    except ValueError:
        offset = 0
    start = monday(today) + timedelta(weeks=offset)
    end = start + timedelta(days=13)  # twee weken overzicht, zodat je een week vooruit ziet
    horizon = today + timedelta(weeks=4)
    plan = plan_for(max(end, horizon))
    changes = plan_changes(plan, today, horizon)
    changed = {c['day']['iso'] for c in changes}
    display = [dict(d, changed=d['iso'] in changed) for d in plan if start <= d['date'] <= end]
    coming = [d for d in plan if d['date'] >= today and not d['done']]
    users = all_users()
    return render_template('index.html', days=display, users=users,
                           names={u['id']: u['name'] for u in users}, ledger=stats(),
                           since=date.fromisoformat(count_from()) if count_from() else None,
                           offset=offset, week_start=start, week_end=end,
                           active=enabled_days(), today=today, short=short,
                           changes=changes, forgotten=forgotten_rides(plan, today),
                           next_ride=coming[0] if coming else None, coming=coming[1:5], week_of=lambda d: (monday(d) - monday(today)).days // 7)


CHANGE_VISIBLE = timedelta(hours=24)


def plan_changes(plan, today, horizon):
    """Welke geplande chauffeurs wijken af van wat deze gebruiker als laatste 'normale' stand zag?

    Per dag bewaren we de chauffeur die de gebruiker kende (driver_id). Wijkt de planning
    daarvan af, dan tonen we 'Gewijzigd' tot maximaal 24 uur na de eerste keer tonen; daarna
    wordt de nieuwe chauffeur de normale stand. Gaat het terug naar de oude chauffeur, dan
    verdwijnt de melding meteen.
    """
    uid = g.user['id']
    now = datetime.now()
    seen = {r['ride_date']: r for r in db().execute(
        'SELECT ride_date, driver_id, shown_at FROM seen_plan WHERE user_id=?', (uid,))}
    changes = []
    for d in plan:
        if d['done'] or not today <= d['date'] <= horizon:
            continue
        row = seen.get(d['iso'])
        known, shown_at = (row['driver_id'], row['shown_at']) if row else (None, None)
        if row is None or known is None or known == d['driver']:
            # Nieuw, eerder nog niemand gepland, of (weer) gelijk aan de bekende stand.
            new_known, new_shown = d['driver'], None
        elif shown_at and now - datetime.fromisoformat(shown_at) > CHANGE_VISIBLE:
            new_known, new_shown = d['driver'], None      # lang genoeg getoond: nieuwe stand
        else:
            changes.append({'day': d, 'was': known, 'now': d['driver']})
            new_known, new_shown = known, shown_at or now.isoformat(timespec='seconds')
        db().execute('INSERT INTO seen_plan(user_id,ride_date,driver_id,shown_at) VALUES (?,?,?,?) '
                     'ON CONFLICT(user_id,ride_date) DO UPDATE SET driver_id=excluded.driver_id, '
                     'shown_at=excluded.shown_at', (uid, d['iso'], new_known, new_shown))
    db().execute('DELETE FROM seen_plan WHERE user_id=? AND ride_date<?', (uid, today.isoformat()))
    db().commit()
    return changes


def forgotten_rides(plan, today):
    """Voorbije carpooldagen (minstens twee mensen) waarvan nog niet is ingevuld wie reed."""
    since = count_from()
    oldest = today - timedelta(days=90)
    return [d for d in plan if d['past'] and not d['done'] and len(d['present']) > 1
            and d['iso'] >= since and d['date'] >= oldest]


def parse_day(raw):
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        abort(400)


def valid_day(raw):
    d = parse_day(raw)
    if d.weekday() not in enabled_days():
        abort(400, 'Dit is geen ingestelde carpooldag.')
    if not date.today() - timedelta(days=90) <= d <= date.today() + timedelta(days=365):
        abort(400, 'Datum buiten het toegestane bereik.')
    return d


@app.post('/attendance/<ride_date>')
@login_required
def set_attendance(ride_date):
    d = valid_day(ride_date)
    if d < date.today():
        abort(400, 'Je kunt je aanwezigheid uit het verleden niet wijzigen.')
    if db().execute('SELECT 1 FROM rides WHERE ride_date=?', (ride_date,)).fetchone():
        abort(400, 'De rit is al bevestigd.')
    going = request.form.get('going')
    if going not in ('0', '1'):
        abort(400)
    db().execute('INSERT INTO attendance(user_id,ride_date,going) VALUES (?,?,?) '
                 'ON CONFLICT(user_id,ride_date) DO UPDATE SET going=excluded.going',
                 (g.user['id'], ride_date, int(going)))
    db().commit()
    audit('attendance', f"{fmt(d)}: {'gaat' if going == '1' else 'gaat niet'}")
    flash('Je aanwezigheid is bijgewerkt.', 'success')
    return back_to(ride_date)


def back_to(ride_date):
    if request.form.get('from') == 'top':   # knop bovenaan de pagina: blijf bovenaan
        return redirect(url_for('index'))
    return redirect(url_for('index', week=request.form.get('week', 0)) + f'#day-{ride_date}')


@app.post('/confirm/<ride_date>')
@login_required
def confirm(ride_date):
    d = valid_day(ride_date)
    if d > date.today():
        abort(400, 'Een toekomstige rit kun je nog niet afronden.')
    if db().execute('SELECT 1 FROM rides WHERE ride_date=?', (ride_date,)).fetchone():
        flash('Deze rit is al afgerond.', 'error')
        return back_to(ride_date)
    ids = {u['id'] for u in all_users()}
    try:
        driver = int(request.form.get('driver', ''))
        passengers = {int(p) for p in request.form.getlist('passengers')}
    except ValueError:
        abort(400)
    if driver not in ids or not passengers <= ids:
        abort(400)
    passengers.discard(driver)
    try:
        db().execute('INSERT INTO rides(ride_date,driver_id,confirmed_at) VALUES (?,?,?)',
                     (ride_date, driver, datetime.now().isoformat(timespec='seconds')))
    except sqlite3.IntegrityError:
        # Iemand anders was op hetzelfde moment net iets eerder.
        db().rollback()
        flash('Deze rit is net al door iemand anders afgerond.', 'error')
        return back_to(ride_date)
    db().executemany('INSERT INTO ride_passengers(ride_date,user_id) VALUES (?,?)',
                     [(ride_date, p) for p in sorted(passengers)])
    # Houd de aanwezigheid in lijn met wat er echt gebeurde.
    for uid in ids:
        db().execute('INSERT INTO attendance(user_id,ride_date,going) VALUES (?,?,?) '
                     'ON CONFLICT(user_id,ride_date) DO UPDATE SET going=excluded.going',
                     (uid, ride_date, int(uid == driver or uid in passengers)))
    db().commit()
    names = {u['id']: u['name'] for u in all_users()}
    audit('ride_confirm', f"{fmt(d)}: {names[driver]} reed, mee: "
                          + (', '.join(names[p] for p in sorted(passengers)) or 'niemand'))
    flash('Rit opgeslagen! De planning voor de volgende dagen is bijgewerkt.', 'success')
    return back_to(ride_date)


@app.post('/no-ride/<ride_date>')
@login_required
def no_ride(ride_date):
    """Er is die dag niet gereden: iedereen op 'niet gegaan', dan verdwijnt de herinnering."""
    d = valid_day(ride_date)
    if d >= date.today():
        abort(400, 'Dit kan alleen voor een dag die al voorbij is.')
    for u in all_users():
        db().execute('INSERT INTO attendance(user_id,ride_date,going) VALUES (?,?,0) '
                     'ON CONFLICT(user_id,ride_date) DO UPDATE SET going=0', (u['id'], ride_date))
    db().commit()
    audit('no_ride', fmt(d))
    flash(f'Opgeslagen: op {fmt(d).lower()} is niet gereden.', 'success')
    return back_to(ride_date)


@app.post('/undo/<ride_date>')
@login_required
def undo(ride_date):
    d = parse_day(ride_date)
    old = db().execute('SELECT u.name FROM rides r LEFT JOIN users u ON u.id=r.driver_id WHERE r.ride_date=?',
                       (ride_date,)).fetchone()
    db().execute('DELETE FROM ride_passengers WHERE ride_date=?', (ride_date,))
    removed = db().execute('DELETE FROM rides WHERE ride_date=?', (ride_date,)).rowcount
    db().commit()
    if removed:
        audit('ride_undo', f"{fmt(d)} (chauffeur was {old['name'] if old and old['name'] else '?'})")
        flash('De rit is ongedaan gemaakt. Je kunt hem opnieuw invullen.', 'success')
    else:
        flash('Deze rit was al ongedaan gemaakt.', 'error')
    return back_to(ride_date)


@app.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'days':
            chosen = request.form.getlist('days')
            if not chosen or any(s not in [str(i) for i in range(7)] for s in chosen):
                flash('Kies minimaal één geldige dag.', 'error')
            else:
                db().execute("UPDATE settings SET value=? WHERE key='weekdays'",
                             (','.join(sorted(set(chosen))),))
                db().commit()
                audit('settings_days', ', '.join(WEEKDAYS[int(i)] for i in sorted(set(chosen))))
                flash('Carpooldagen aangepast.', 'success')
        elif action == 'count_from':
            raw = request.form.get('count_from', '').strip()
            try:
                if raw:
                    date.fromisoformat(raw)
            except ValueError:
                flash('Dat is geen geldige datum.', 'error')
                return redirect(url_for('settings'))
            db().execute("INSERT INTO settings(key,value) VALUES ('count_from',?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (raw,))
            db().commit()
            audit('settings_count_from', fmt(date.fromisoformat(raw)) if raw else 'leeg: alle ritten tellen mee')
            flash('Startdatum voor de telling opgeslagen.' if raw else 'Alle ritten tellen weer mee.', 'success')
        elif action == 'add_user':
            name = clean_name(request.form.get('name', ''))
            pw = request.form.get('password', '')
            if not 2 <= len(name) <= 60 or len(pw) < 12:
                flash('Naam 2 tot 60 tekens, tijdelijk wachtwoord minimaal 12 tekens.', 'error')
            elif find_user(name):
                flash('Deze naam bestaat al.', 'error')
            else:
                try:
                    db().execute('INSERT INTO users(name,password_hash,must_change_password) VALUES (?,?,1)',
                                 (name, generate_password_hash(pw)))
                    db().commit()
                    audit('user_add', name)
                    flash(f'Account voor {name} gemaakt. Deel de inloggegevens persoonlijk.', 'success')
                except sqlite3.IntegrityError:
                    flash('Deze naam bestaat al.', 'error')
        elif action in ('feedback_done', 'feedback_open'):
            changed = db().execute('UPDATE feedback SET done=? WHERE id=?',
                                   (int(action == 'feedback_done'), request.form.get('feedback_id', ''))).rowcount
            db().commit()
            if changed:
                audit(action, f"bericht #{request.form.get('feedback_id', '')}")
            return redirect(url_for('settings') + '#feedback')
        elif action == 'reset_password':
            user = db().execute('SELECT name FROM users WHERE id=?',
                                (request.form.get('user_id', ''),)).fetchone()
            pw = request.form.get('password', '')
            if not user or len(pw) < 12:
                flash('Kies een collega en een tijdelijk wachtwoord van minimaal 12 tekens.', 'error')
            else:
                db().execute('UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?',
                             (generate_password_hash(pw), request.form['user_id']))
                db().commit()
                audit('password_reset', user['name'])
                flash(f'Tijdelijk wachtwoord voor {user["name"]} ingesteld.', 'success')
        return redirect(url_for('settings'))
    feedback = db().execute(
        'SELECT f.*, u.name FROM feedback f LEFT JOIN users u ON u.id=f.user_id '
        'ORDER BY f.done, f.id DESC LIMIT 50').fetchall()
    return render_template('settings.html', active=enabled_days(), users=all_users(),
                           count_from=count_from(), today=date.today(), feedback=feedback,
                           fmt_dt=lambda v: datetime.fromisoformat(v).strftime('%d-%m-%Y %H:%M'))


# --- Agenda-abonnement (iCalendar) -----------------------------------------------
CAL_PAST = timedelta(days=60)     # zoveel terug staat in de agenda
CAL_FUTURE = timedelta(weeks=8)   # en zoveel vooruit


def ical_escape(text):
    return (text.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
            .replace('\n', '\\n'))


def ical_fold(line):
    """Regels langer dan 75 bytes vouwen, zoals de iCalendar-standaard (RFC 5545) vraagt."""
    out, cur = [], ''
    for ch in line:
        if len((cur + ch).encode('utf-8')) > (75 if not out else 74):
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return '\r\n '.join(out)


def calendar_token(user, renew=False):
    token = user['cal_token']
    if renew or not token:
        token = secrets.token_urlsafe(24)
        db().execute('UPDATE users SET cal_token=? WHERE id=?', (token, user['id']))
        db().commit()
    return token


def calendar_feed(user):
    today = date.today()
    names = {u['id']: u['name'] for u in all_users()}
    me = user['id']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    host = request.host.split(':')[0]
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Meerijden//Carpool//NL', 'CALSCALE:GREGORIAN',
             'METHOD:PUBLISH', 'X-WR-CALNAME:Carpool', 'X-WR-TIMEZONE:Europe/Amsterdam',
             'REFRESH-INTERVAL;VALUE=DURATION:PT12H', 'X-PUBLISHED-TTL:PT12H']
    for d in plan_for(today + CAL_FUTURE):
        if d['date'] < today - CAL_PAST:
            continue
        passengers = [u['id'] for u in d['passengers']]
        if d['done']:
            if me != d['driver'] and me not in passengers:
                continue
            summary = '✓ Jij reed' if d['driver'] == me else f"✓ {names.get(d['driver'], '?')} reed (jij reed mee)"
            others = [names[p] for p in passengers if p != me and p in names]
        else:
            if d['responses'].get(me) != 1 or d.get('solo'):
                continue      # alleen dagen waarop je gaat en er echt gecarpoold wordt
            if d['driver'] == me:
                summary = '🚗 Jij rijdt'
            elif d['driver']:
                summary = f"🚗 {names[d['driver']]} rijdt (jij rijdt mee)"
            else:
                summary = '🚗 Carpool'
            others = [u['name'] for u in d['present'] if u['id'] != me]
        desc = []
        others = [o for o in others if o != names.get(d['driver'])]
        if others:
            desc.append(('Je neemt mee: ' if d['driver'] == me else 'Ook mee: ') + ', '.join(others))
        if not d['done']:
            desc.append('Gepland; dit kan nog veranderen als iemand zijn aanwezigheid aanpast.')
        desc.append(url_for('index', _external=True))
        end = d['date'] + timedelta(days=1)
        lines += ['BEGIN:VEVENT', f"UID:carpool-{d['iso']}-{me}@{host}", f'DTSTAMP:{stamp}',
                  f"DTSTART;VALUE=DATE:{d['date']:%Y%m%d}", f'DTEND;VALUE=DATE:{end:%Y%m%d}',
                  f'SUMMARY:{ical_escape(summary)}', f"DESCRIPTION:{ical_escape(chr(10).join(desc))}",
                  f"URL:{url_for('index', _external=True)}", 'TRANSP:TRANSPARENT',
                  'STATUS:CONFIRMED' if d['done'] else 'STATUS:TENTATIVE', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return '\r\n'.join(ical_fold(line) for line in lines) + '\r\n'


@app.route('/agenda/<token>.ics')
def calendar_ics(token):
    """Openbare (geheime) link voor agenda-apps; die kunnen niet inloggen."""
    if len(token) < 20:
        abort(404)
    user = db().execute('SELECT * FROM users WHERE cal_token=?', (token,)).fetchone()
    if not user:
        abort(404)
    g.user = user
    resp = app.response_class(calendar_feed(user), mimetype='text/calendar')
    resp.headers['Content-Disposition'] = 'inline; filename="carpool.ics"'
    return resp


@app.route('/agenda', methods=['GET', 'POST'])
@login_required
def calendar_page():
    renew = request.method == 'POST' and request.form.get('action') == 'renew'
    token = calendar_token(g.user, renew=renew)
    if renew:
        audit('calendar_renew')
        flash('Nieuwe agendalink gemaakt. De oude link werkt niet meer.', 'success')
        return redirect(url_for('calendar_page'))
    https_url = url_for('calendar_ics', token=token, _external=True)
    webcal_url = 'webcal://' + https_url.split('://', 1)[1]
    add_from_web = '/calendar/0/addfromweb?name=Carpool&url=' + quote(https_url, safe='')
    return render_template('agenda.html', https_url=https_url, webcal_url=webcal_url,
                           google_url='https://calendar.google.com/calendar/r?cid=' + quote(https_url, safe=''),
                           outlook_url='https://outlook.live.com' + add_from_web,
                           outlook_work_url='https://outlook.office.com' + add_from_web)


FEEDBACK_MAX_LEN = 2000
FEEDBACK_PER_DAY = 10


@app.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        since = (datetime.now() - timedelta(days=1)).isoformat(timespec='seconds')
        recent = db().execute('SELECT COUNT(*) FROM feedback WHERE user_id=? AND created_at>?',
                              (g.user['id'], since)).fetchone()[0]
        if len(message) < 3:
            flash('Schrijf even wat je kwijt wilt.', 'error')
        elif len(message) > FEEDBACK_MAX_LEN:
            flash(f'Je bericht is te lang (maximaal {FEEDBACK_MAX_LEN} tekens).', 'error')
        elif recent >= FEEDBACK_PER_DAY:
            flash('Je hebt vandaag al veel feedback gestuurd. Probeer het morgen nog eens.', 'error')
        else:
            db().execute('INSERT INTO feedback(user_id, created_at, message) VALUES (?,?,?)',
                         (g.user['id'], datetime.now().isoformat(timespec='seconds'), message))
            db().commit()
            audit('feedback_sent', message[:80] + ('…' if len(message) > 80 else ''))
            flash('Bedankt voor je feedback! De beheerder krijgt hem te zien.', 'success')
            return redirect(url_for('index'))
        return render_template('feedback.html', message=message, max_len=FEEDBACK_MAX_LEN)
    return render_template('feedback.html', message='', max_len=FEEDBACK_MAX_LEN)


AUDIT_PAGE_SIZE = 100


@app.route('/audit')
@admin_required
def audit_page():
    where, args = [], []
    who = request.args.get('wie', '')
    cat = request.args.get('soort', '')
    if who == 'anoniem':
        where.append('user_id IS NULL')
    elif who.isdigit():
        where.append('user_id=?'); args.append(int(who))
    if cat in AUDIT_CATEGORIES:
        acts = [a for a, (c, _) in AUDIT_ACTIONS.items() if c == cat]
        where.append('action IN (%s)' % ','.join('?' * len(acts))); args += acts
    before = request.args.get('voor', '')
    if before.isdigit():
        where.append('id<?'); args.append(int(before))
    # Alleen vaste stukjes SQL; alle gebruikersinvoer gaat via ?-parameters.
    sql = 'SELECT * FROM audit_log' + (' WHERE ' + ' AND '.join(where) if where else '')  # nosec B608
    rows = db().execute(sql + ' ORDER BY id DESC LIMIT ?', args + [AUDIT_PAGE_SIZE + 1]).fetchall()
    more = len(rows) > AUDIT_PAGE_SIZE
    rows = rows[:AUDIT_PAGE_SIZE]
    return render_template('audit.html', rows=rows, more=more, who=who, cat=cat, users=all_users(),
                           categories=AUDIT_CATEGORIES, actions=AUDIT_ACTIONS, keep_days=AUDIT_KEEP_DAYS,
                           fmt_dt=lambda v: datetime.fromisoformat(v).strftime('%d-%m-%Y %H:%M'),
                           next_url=url_for('audit_page', wie=who or None, soort=cat or None,
                                            voor=rows[-1]['id']) if more else None)


@app.route('/password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old = request.form.get('old', '')
        new = request.form.get('new', '')
        if not check_password_hash(g.user['password_hash'], old):
            flash('Huidige wachtwoord is onjuist.', 'error')
        elif len(new) < 12:
            flash('Nieuw wachtwoord moet minimaal 12 tekens lang zijn.', 'error')
        else:
            db().execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                         (generate_password_hash(new), g.user['id']))
            db().commit()
            # Andere apparaten worden uitgelogd; dit apparaat blijft ingelogd.
            start_session(db().execute('SELECT * FROM users WHERE id=?', (g.user['id'],)).fetchone())
            audit('password_change')
            flash('Wachtwoord gewijzigd.', 'success')
            return redirect(url_for('index'))
    return render_template('password.html')


if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '5000')),
            debug=False)
