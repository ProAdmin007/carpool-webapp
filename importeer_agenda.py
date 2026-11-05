"""Importeer een carpoolagenda (.ics) in de app.

Gebruik:  python importeer_agenda.py agenda.ics [--admin Anna]

- Ritten vóór vandaag worden als afgeronde ritten opgeslagen (chauffeur + meerijders).
- Ritten vanaf vandaag worden alleen als aanwezigheid opgeslagen; de app bepaalt zelf
  wie er rijdt op basis van ieders eerlijk aandeel.
- Onbekende namen krijgen een account met een tijdelijk wachtwoord (wordt getoond).
Het script is veilig om opnieuw te draaien: bestaande datums worden overschreven.
"""
import argparse
import re
import secrets
from datetime import date, datetime

from werkzeug.security import generate_password_hash

import app as carpool


def parse_ics(text):
    text = re.sub(r'\r?\n[ \t]', '', text)  # gevouwen regels samenvoegen
    events = []
    for block in re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', text, re.S):
        fields = {}
        for line in block.strip().splitlines():
            key, _, value = line.partition(':')
            fields[key.split(';')[0]] = value
        day = datetime.strptime(fields['DTSTART'][:8], '%Y%m%d').date()
        desc = fields.get('DESCRIPTION', '').replace('\\\\n', '\n').replace('\\n', '\n').replace('\\,', ',')
        m = re.search(r'Chauffeur:\s*([^\n]+)', desc) or re.search(r'[-–]\s*(\S+)\s+rijdt', fields.get('SUMMARY', ''))
        driver = m.group(1).strip()
        m = re.search(r'Meerijders:\s*([^\n]*)', desc)
        riders = re.sub(r'\(.*?\)', '', m.group(1)) if m else ''
        passengers = [n.strip() for n in riders.split(',') if n.strip() and n.strip() != driver]
        events.append((day, driver, passengers))
    return sorted(events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ics')
    ap.add_argument('--admin', help='naam die beheerder wordt (als die nog niet bestaat)')
    args = ap.parse_args()
    events = parse_ics(open(args.ics, encoding='utf-8').read())
    today = date.today()

    with carpool.app.app_context():
        carpool.init_db()
        db = carpool.db()
        names = sorted({n for _, d, p in events for n in [d, *p]} | ({args.admin} if args.admin else set()))
        for name in names:
            if not db.execute('SELECT 1 FROM users WHERE name=?', (name,)).fetchone():
                pw = secrets.token_urlsafe(9)
                db.execute('INSERT INTO users(name,password_hash,is_admin,must_change_password) VALUES (?,?,?,1)',
                           (name, generate_password_hash(pw), int(name == args.admin)))
                print(f'Account aangemaakt: {name:<10} tijdelijk wachtwoord: {pw}'
                      + ('  (beheerder)' if name == args.admin else ''))
        ids = {r['name']: r['id'] for r in db.execute('SELECT id, name FROM users')}

        for day, driver, passengers in events:
            iso = day.isoformat()
            going = {driver, *passengers}
            for name, uid in ids.items():
                db.execute('INSERT INTO attendance(user_id,ride_date,going) VALUES (?,?,?) '
                           'ON CONFLICT(user_id,ride_date) DO UPDATE SET going=excluded.going',
                           (uid, iso, int(name in going)))
            if day < today:
                db.execute('DELETE FROM ride_passengers WHERE ride_date=?', (iso,))
                db.execute('DELETE FROM rides WHERE ride_date=?', (iso,))
                db.execute('INSERT INTO rides(ride_date,driver_id,confirmed_at) VALUES (?,?,?)',
                           (iso, ids[driver], datetime.now().isoformat(timespec='seconds')))
                db.executemany('INSERT INTO ride_passengers(ride_date,user_id) VALUES (?,?)',
                               [(iso, ids[p]) for p in passengers])
                print(f'{iso}  rit       {driver} reed, mee: {", ".join(passengers) or "-"}')
            else:
                print(f'{iso}  gepland   aanwezig: {", ".join(sorted(going))}')
        db.commit()


if __name__ == '__main__':
    main()
