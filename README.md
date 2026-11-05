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

- De app luistert op poort `5000` van de server. Je reverse proxy stuurt het https-verkeer daarnaartoe. Draait de proxy op dezelfde machine, dan kun je de poort afschermen voor de rest van het netwerk met `CARPOOL_BIND=127.0.0.1:5000`.
- Database en geheime sleutel staan in het Docker-volume `carpool-data` (`/data` in de container). Ze blijven bewaard bij updates en herstarts.
- Inloggen werkt via https (de reverse proxy) en ook direct via `http://<server-ip>:5000`. Via https krijgt de inlogcookie automatisch het label "alleen https". Wil je inloggen via http helemaal blokkeren, zet dan `COOKIE_SECURE=1`.
- De container gebruikt Nederlandse tijd (`TZ=Europe/Amsterdam`), dus "vandaag" wisselt om middernacht.

### Deployen met Dockhand (makkelijkst)

Met [Dockhand](https://github.com/Finsys/dockhand) haal je de app rechtstreeks uit deze GitHub-repo. Dockhand bouwt de image en zet bij elke nieuwe versie automatisch de update neer.

1. Ga in Dockhand naar **Stacks** en kies **Deploy from Git**.
2. Kies bij **Repository** voor **Add new**:
   - **Repository URL:** `https://github.com/ProAdmin007/carpool-webapp.git`
   - **Branch:** `main`
   - **Credential:** **None (public)**
3. Vul de stack in:
   - **Stack name:** `carpool`
   - **Compose file path:** `docker-compose.yml`
   - Laat **Build images on deploy** aangevinkt. Dockhand zet dit zelf aan omdat de compose-file een `build:`-sectie heeft.
4. Kies hoe updates binnenkomen:
   - **Enable scheduled sync:** Dockhand kijkt zelf regelmatig of er iets nieuws op GitHub staat. Dit werkt altijd, ook als Dockhand niet vanaf internet bereikbaar is.
   - **Of Enable webhook:** GitHub meldt een nieuwe versie direct, maar dan moet Dockhand vanaf internet bereikbaar zijn. Klik op **Generate secret** en maak de stack aan. Daarna kopieer je de **Webhook URL** en zet je die in GitHub onder **Settings → Webhooks → Add webhook**:
     - **Payload URL:** de gekopieerde Webhook URL
     - **Content type:** `application/json`
     - **Secret:** het gegenereerde secret
     - Kies **Just the push event**
5. Vink **Deploy now** aan en maak de stack aan.
6. Laat je reverse proxy naar de app wijzen (zie hieronder).

**Instellingen aanpassen** doe je in de omgevingsvariabelen-editor van de stack in Dockhand. Je hoeft de repo daarvoor niet te wijzigen.

| Variabele | Standaard | Wanneer aanpassen |
|---|---|---|
| `CARPOOL_BIND` | `5000` | Andere poort nodig? Gebruik bijvoorbeeld `8085`. Draait de proxy op dezelfde machine en wil je de app niet direct bereikbaar maken in het netwerk, gebruik dan `127.0.0.1:5000`. |
| `COOKIE_SECURE` | `auto` | Zet op `1` als inloggen alleen via https mag. Inloggen via `http://` lukt dan niet meer. |
| `TRUST_PROXY` | `1` | Alleen op `0` zetten als er geen reverse proxy voor de app staat. |
| `TRUSTED_PROXY` | `*` (iedereen) | Vul het IP-adres van je reverse proxy in. Dan accepteert de app alleen van die proxy headers als `X-Forwarded-For`. |

**Bestaande database meenemen** (eenmalig, op de Docker-host):

```bash
docker cp carpool.sqlite3 carpool:/data/carpool.sqlite3
docker exec -u root carpool chown carpool /data/carpool.sqlite3
docker restart carpool
```

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

Draait je proxy zelf ook in Docker (bijvoorbeeld Traefik of Nginx Proxy Manager)? Laat de proxy dan naar `<server-ip>:5000` wijzen. Of zet beide containers in hetzelfde Docker-netwerk en laat de proxy naar `carpool:5000` wijzen.

**Bestaande database meenemen**

```bash
docker compose cp carpool.sqlite3 carpool:/data/carpool.sqlite3
docker compose exec -u root carpool chown carpool /data/carpool.sqlite3
docker compose restart
```

**Wachtwoord kwijt?**

Een collega kan de beheerder een nieuw wachtwoord geven via **Instellingen → Wachtwoord vergeten?**. Is de beheerder zelf zijn wachtwoord kwijt, draai dan op de Docker-host:

```bash
docker exec carpool python reset_wachtwoord.py NAAM
```

Het script toont een tijdelijk wachtwoord. Bij de volgende login kies je zelf een nieuw wachtwoord.

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
4. Iedereen heeft een **saldo**. De chauffeur krijgt +1 per meerijder en iedere meerijder krijgt −1. De app houdt ook per tweetal bij wie wie heeft gereden. Van de mensen die die dag gaan, rijdt degene die **bij de anderen van die dag het meest achterstaat**. Wie er niet is telt die dag niet mee, zodat niemand de schuld van een afwezige collega afbetaalt. Bij gelijkstand rijdt wie het langst niet heeft gereden.
5. De planning voor de komende dagen wordt meteen opnieuw berekend. Een verkeerd ingevulde rit maak je ongedaan met **Klopt niet? Ongedaan maken**.

Voorbeeld: Anna rijdt met Bas en Chris mee. Anna staat dan op +2, Bas en Chris op −1. Daarna rijdt Bas of Chris. Rijdt Anna alleen met Bas, dan krijgt alleen Bas er −1 bij.

Gaat er op een dag maar één persoon, dan is er geen carpool. De app toont dan **Geen carpool – alleen … gaat**, wijst geen chauffeur aan, vraagt achteraf niet om de rit in te vullen en zet niets in de agenda.

Een dag die al voorbij is en niet is afgerond, telt niet mee. Je kunt hem achteraf nog afronden (tot 90 dagen terug).

**Windows, nog makkelijker:** dubbelklik op `start.bat`. Bij de eerste keer installeert die alles automatisch.

## Ritten in je eigen agenda

Onderaan de planning staat **Ritten in je eigen agenda**. Daar vindt iedereen een persoonlijke abonnementslink (`/agenda/<geheime-code>.ics`). Die voeg je één keer toe aan Google Agenda, Outlook of de iPhone-/Mac-agenda. De agenda-app haalt de planning daarna zelf op. De app vraagt om elke 12 uur te verversen. Google ververst meestal één à twee keer per dag.

- In de agenda staan de dagen waarop je gaat, als hele-dag-afspraak die je niet als bezet toont. Ritten tot 8 weken vooruit en 60 dagen terug.
- Wie de link heeft, kan je ritten zien. Lekt hij uit, klik dan op **Nieuwe link maken**. De oude link werkt dan direct niet meer.

## Feedback

Iedereen kan via **💬 Feedback** een bericht sturen. Op de planning staat dat onderaan als kaart, op andere pagina's als link onderaan. De beheerder ziet de berichten onder **Instellingen → Feedback**, met een rode teller in het menu zolang er openstaande berichten zijn, en zet ze daar op **Afgehandeld**. Je kunt maximaal 10 berichten per dag sturen, van hooguit 2000 tekens.

## Auditlog

De beheerder vindt onder **Instellingen → 📋 Auditlog bekijken** wie wat heeft gedaan en wanneer. Je kunt filteren op persoon (of "Niet ingelogd") en op soort.

- **Inloggen:** ingelogd, mislukte pogingen (met de naam die werd geprobeerd), blokkades en uitloggen.
- **Ritten:** rit afgerond (wie reed, wie reed mee), ongedaan gemaakt, niet gereden.
- **Aanwezigheid:** Ja/Nee per dag.
- **Instellingen:** carpooldagen, startdatum van de telling, collega toegevoegd, wachtwoord van een collega gereset.
- **Account:** eigen wachtwoord gewijzigd, nieuwe agendalink, wachtwoord gereset via `reset_wachtwoord.py`.
- **Feedback:** verstuurd, afgehandeld of heropend.

Per regel worden het IP-adres en het soort apparaat (iPhone, Android, Windows, …) bewaard. Achter Cloudflare komt het IP-adres uit de header `CF-Connecting-IP`. Zie het als een aanwijzing, niet als bewijs. Wachtwoorden worden nooit gelogd. Regels ouder dan 365 dagen worden automatisch verwijderd.

## Oude agenda importeren

`python importeer_agenda.py agenda.ics --admin Anna` zet ritten uit een .ics-agenda in de app. Ritten van vóór vandaag worden afgeronde ritten, met chauffeur en meerijders. Ritten vanaf vandaag worden alleen aanwezigheid; wie er rijdt, rekent de app zelf uit. Namen die nog niet bestaan krijgen een account met een tijdelijk wachtwoord. Je kunt het script veilig opnieuw draaien.

## Technische opzet

- `app.py` – alle Flask-routes, SQLite-database, authenticatie en eerlijke planning (`Ledger` + `plan_for`)
- `start.bat` – dubbelklik-start voor Windows
- `start.py` – Waitress-webserver (met `TRUST_PROXY=1` achter een reverse proxy)
- `Dockerfile`, `docker-compose.yml` – container met de data in het volume `/data`
- `tests/` – tests, draaien met `python tests/test_rare_acties.py`, `python tests/test_meldingen.py` en `python tests/test_beveiliging.py`
- `sbom.cdx.json`, `pyproject.toml` – SBOM en projectgegevens
- `reset_wachtwoord.py` – tijdelijk wachtwoord instellen vanaf de server
- `templates/` – Nederlandstalige pagina's
- `static/style.css` – interface, ook geschikt voor telefoon
- `carpool.sqlite3` – lokale database (wordt bij de eerste start automatisch gemaakt)

## Beveiliging

- **Wachtwoorden** worden opgeslagen als hash (scrypt via Werkzeug). Een wachtwoord moet minimaal 12 tekens hebben.
- **Wachtwoorden raden** wordt afgeremd. Na 5 foute pogingen op één naam, of 30 vanaf één IP-adres, is inloggen een kwartier geblokkeerd. Een onbekende naam kost evenveel rekentijd als een bekende, zodat je niet kunt achterhalen welke namen bestaan.
- **Sessies:** de inlogcookie is ondertekend, `HttpOnly` en `SameSite=Lax`. Via https krijgt hij ook het label `Secure`. Wijzig je je wachtwoord, of reset de beheerder het, dan worden alle andere apparaten uitgelogd.
- **Elk formulier** heeft een CSRF-token. Databasequery's zijn geparametriseerd. Alle invoer wordt ge-escaped.
- **Headers:** een strikte Content-Security-Policy met nonce (geen inline scripts of styles), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS via https, en `Cache-Control: no-store` voor pagina's met persoonlijke gegevens.
- **In Docker** draait de app als gewone gebruiker, niet als root. `secret_key.txt` is alleen leesbaar voor die gebruiker.

**SBOM en controle van afhankelijkheden**

`requirements.txt` gebruikt vaste versies. `sbom.cdx.json` is de bijbehorende SBOM (CycloneDX 1.6). Opnieuw maken en controleren op bekende kwetsbaarheden:

```bash
pip install cyclonedx-bom pip-audit bandit
cyclonedx-py requirements requirements.txt --pyproject pyproject.toml --mc-type application --sv 1.6 --of JSON --output-reproducible -o sbom.cdx.json
pip-audit -r requirements.txt
bandit -r . -x ./.venv,./tests
```

De SBOM beschrijft de Python-pakketten van de app. Wil je ook de onderdelen van de Docker-basisimage zien, gebruik dan bijvoorbeeld `docker sbom` of `syft` op de gebouwde image.

## Mogelijke uitbreidingen

Automatische herinnering om de volgende week in te vullen, feestdagen/vakanties, notificaties, of apart rijden voor heen- en terugreis. Begin met deze simpele variant om te zien hoe jullie hem gebruiken.

## ☕ Support

Vind je dit handig? Trakteer me op een koffie:

<a href="https://ko-fi.com/proadmin007"><img alt="Buy me a coffee on Ko-fi" src="https://ko-fi.com/img/githubbutton_sm.svg"></a>
