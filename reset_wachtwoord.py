"""Geef een gebruiker een nieuw tijdelijk wachtwoord (bijv. als de beheerder het kwijt is).

Gebruik:
    python reset_wachtwoord.py NAAM
    docker exec carpool python reset_wachtwoord.py NAAM      (in Docker)

Het nieuwe wachtwoord wordt getoond; bij de volgende login moet de gebruiker zelf een nieuw kiezen.
Zonder NAAM worden alle accounts getoond.
"""
import secrets
import sys

import app as carpool
from werkzeug.security import generate_password_hash


def main():
    with carpool.app.app_context():
        carpool.init_db()
        if len(sys.argv) < 2:
            print('Accounts:', ', '.join(u['name'] for u in carpool.all_users()) or '(geen)')
            print('Gebruik: python reset_wachtwoord.py NAAM')
            return 1
        user = carpool.find_user(' '.join(sys.argv[1:]))
        if not user:
            print(f'Geen account gevonden met de naam "{" ".join(sys.argv[1:])}".')
            print('Accounts:', ', '.join(u['name'] for u in carpool.all_users()) or '(geen)')
            return 1
        password = secrets.token_urlsafe(12)
        carpool.db().execute('UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?',
                             (generate_password_hash(password), user['id']))
        carpool.db().commit()
        print(f'Nieuw tijdelijk wachtwoord voor {user["name"]}: {password}')
        print('Bij de volgende login wordt gevraagd een eigen wachtwoord te kiezen.')
        return 0


if __name__ == '__main__':
    sys.exit(main())
