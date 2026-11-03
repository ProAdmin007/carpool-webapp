# Meerijden — eenvoudige carpoolplanner

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3-000000?logo=flask&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-steun%20mij-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/proadmin007)

Een kleine Nederlandstalige, mobielvriendelijke webapp in Python, Flask en SQLite. Geen betaalde API's, geen frontend-buildproces of externe accounts.

## Snel starten

Installeer Python 3.11 of nieuwer. Open een terminal in deze map:

```bash
python -m venv .venv
```

**Windows**

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
python start.py
```

**Linux/macOS**

```bash
source .venv/bin/activate
pip install -r requirements.txt
python start.py
```

Open **http://127.0.0.1:5000**. De eerste bezoeker maakt via `/setup` het beheerdersaccount aan. Voeg de twee andere collega's toe onder Instellingen. Geef de tijdelijke wachtwoorden persoonlijk door; de collega's moeten die wijzigen na hun eerste login.

## Draaien met Docker achter een reverse proxy

```bash
git clone https://github.com/ProAdmin007/carpool-webapp.git
cd carpool-webapp
docker compose up -d --build
```

- De app luistert op `127.0.0.1:5000` van de server. Je reverse proxy stuurt het https-verkeer daarnaartoe.
- Database en geheime sleutel staan in het Docker-volume `carpool-data` (`/data` in de container). Ze blijven bewaard bij updates en herstarts.
- Inloggen werkt alleen via **https**, omdat `COOKIE_SECURE=1` staat. Wil je eerst zonder proxy testen, zet dan in `docker-compose.yml` `COOKIE_SECURE` op `"0"` en de poort op `"5000:5000"`.
- De container gebruikt Nederlandse tijd (`TZ=Europe/Amsterdam`), dus "vandaag" wisselt om middernacht.

**Bijwerken naar een nieuwe versie**

```bash
git pull && docker compose up -d --build
```

**Voorbeeld reverse proxy**

Caddy (regelt https automatisch):

```
carpool.jouwdomein.nl {
    reverse_proxy 127.0.0.1:5000
}
```

nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Draait je proxy zelf ook in Docker (bijvoorbeeld Traefik of Nginx Proxy Manager)? Zet beide containers dan in hetzelfde Docker-netwerk, laat de proxy naar `carpool:5000` wijzen en haal het `ports:`-blok weg.

**Bestaande database meenemen**

```bash
docker compose cp carpool.sqlite3 carpool:/data/carpool.sqlite3
docker compose exec -u root carpool chown carpool /data/carpool.sqlite3
docker compose restart
```

**Back-up maken**

```bash
docker compose exec carpool python -c "import sqlite3; sqlite3.connect('/data/carpool.sqlite3').backup(sqlite3.connect('/data/backup.sqlite3'))"
docker compose cp carpool:/data/backup.sqlite3 ./backup.sqlite3
```

## Op een server zonder Docker

- Voor gebruik over het bedrijfsnetwerk: start met `HOST=0.0.0.0` (PowerShell: `$env:HOST='0.0.0.0'`; daarna `python start.py`). Gebruik de servernaam en poort 5000. **Doe dit alleen op een vertrouwd netwerk**.
- Voor toegang via internet: **plaats de app achter een HTTPS-reverse-proxy**, met een vaste `SECRET_KEY` en `COOKIE_SECURE=1`, en beperk toegang bij voorkeur tot VPN/Tailscale. Zet poort 5000 niet rechtstreeks open op internet.
- Iedereen blijft ingelogd, ook na een herstart. Daarvoor maakt de app bij de eerste start `secret_key.txt` aan. Houd dat bestand geheim en verwijder het niet. Verwijder je het toch, dan moet iedereen één keer opnieuw inloggen. Je kunt ook zelf een vaste `SECRET_KEY` instellen als omgevingsvariabele.
- Sla de app + `carpool.sqlite3` op in een persistent volume en maak regelmatig back-ups. Maak een consistente SQLite-back-up (bijvoorbeeld via SQLite `.backup` of Python `sqlite3.Connection.backup`), niet zomaar een kopie tijdens actieve writes.
- Werk Flask en Waitress periodiek bij.

## Zo werkt de planning

1. De beheerder kiest de vaste dagen onder **Instellingen** (standaard maandag en donderdag). Een dag erbij is één vinkje.
2. Iedereen klikt per dag op **Ja, ik ga** of **Nee**. Je ziet twee weken vooruit, dus je kunt een week van tevoren invullen.
3. Na de rit klikt iemand op **Rit afronden: wie reed er?**, kiest wie er reed en vinkt aan wie er meereed. Dat mag iemand anders zijn dan de geplande chauffeur.
4. Iedereen heeft een **saldo**. De chauffeur krijgt +1 per meerijder en iedere meerijder krijgt −1. Van de mensen die die dag gaan, rijdt degene met het **laagste saldo**. Bij gelijkstand rijdt wie het langst niet heeft gereden.
5. De planning voor de komende dagen wordt meteen opnieuw berekend. Een verkeerd ingevulde rit maak je ongedaan met **Klopt niet? Ongedaan maken**.

Voorbeeld: Anna rijdt met Bas en Chris mee. Anna staat dan op +2, Bas en Chris op −1. Daarna rijdt Bas of Chris. Rijdt Anna alleen met Bas, dan krijgt alleen Bas er −1 bij.

Een dag die al voorbij is en niet is afgerond, telt niet mee. Je kunt hem achteraf nog afronden (tot 90 dagen terug).

**Windows, nog makkelijker:** dubbelklik op `start.bat`. Bij de eerste keer installeert die alles automatisch.

## Oude agenda importeren

`python importeer_agenda.py agenda.ics --admin Anna` zet ritten uit een .ics-agenda in de app. Ritten van vóór vandaag worden afgeronde ritten, met chauffeur en meerijders. Ritten vanaf vandaag worden alleen aanwezigheid; wie er rijdt, rekent de app zelf uit. Namen die nog niet bestaan krijgen een account met een tijdelijk wachtwoord. Je kunt het script veilig opnieuw draaien.

## Technische opzet

- `app.py` – alle Flask-routes, SQLite-database, authenticatie en eerlijke planning (`Ledger` + `plan_for`)
- `start.bat` – dubbelklik-start voor Windows
- `start.py` – Waitress-webserver (met `TRUST_PROXY=1` achter een reverse proxy)
- `Dockerfile`, `docker-compose.yml` – container met de data in het volume `/data`
- `tests/` – tests, draaien met `python tests/test_rare_acties.py` en `python tests/test_meldingen.py`
- `templates/` – Nederlandstalige pagina's
- `static/style.css` – interface, ook geschikt voor telefoon
- `carpool.sqlite3` – lokale database (wordt bij de eerste start automatisch gemaakt)

## Mogelijke uitbreidingen

Automatische herinnering om de volgende week in te vullen, feestdagen/vakanties, notificaties, of apart rijden voor heen- en terugreis. Begin met deze simpele variant om te zien hoe jullie hem gebruiken.

## ☕ Support

Vind je dit handig? Trakteer me op een koffie:

<a href="https://ko-fi.com/proadmin007"><img alt="Buy me a coffee on Ko-fi" src="https://ko-fi.com/img/githubbutton_sm.svg"></a>
