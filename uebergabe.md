# Übergabe: Discord-Bot + Web-Dashboard (MEE6-Klon)

Stand: 2026-11-01. Dieses Dokument richtet sich an eine andere KI/Entwicklungsinstanz,
die an diesem Projekt weiterarbeiten soll, ohne den bisherigen Chatverlauf zu kennen.

## Ziel des Projekts

Ein selbst gehosteter Discord-Bot (discord.py, Slash-Commands) mit Web-Dashboard
(Flask + Discord-OAuth2), der schrittweise Kernfunktionen von MEE6 nachbildet.
Bot und Dashboard laufen **in einem einzigen Docker-Container** als zwei Prozesse
und werden auf einem Unraid-Server deployed.

## Architektur

```
Discord-host/
├── bot.py                        # Discord-Bot-Entrypoint, lädt alle Cogs automatisch
├── cogs/
│   ├── welcome.py                 # Willkommensnachricht (konfigurierbarer Channel)
│   ├── autorole.py                # Automatische Rollenvergabe bei Join
│   └── internal_api.py            # Interne aiohttp-API für Bot<->Dashboard-Kommunikation
├── database/
│   └── db.py                      # Gemeinsame SQLite-DB (aiosqlite), eine Tabelle: guild_config
├── dashboard/
│   ├── app.py                     # Flask-App: OAuth2-Login, Server-Übersicht, Embed-Generator
│   └── templates/
│       ├── index.html             # Login-Seite
│       ├── dashboard.html         # Server-Übersicht (Admin-Guilds des eingeloggten Users)
│       └── embed_generator.html   # Formular zum Senden eines Embeds über den Bot
├── Dockerfile                     # Ein Image für Bot + Dashboard
├── start.sh                       # Startet beide Prozesse im Container, leitet SIGTERM weiter
├── docker-compose.yml             # Lokales Referenz-Setup (für produktives Deployment siehe deploy.sh)
├── deploy.sh                      # Tatsächlich genutztes Deploy-Skript (SSH + docker run auf Unraid)
├── requirements.txt
├── .env.example                   # Vorlage, welche Env-Vars nötig sind
└── .env                           # Lokal ausgefüllt, NICHT eingecheckt (siehe .gitignore)
```

Kein Git-Repo bisher initialisiert (`git init` wurde nie ausgeführt).

## Features (Stand jetzt)

### 1. Willkommensnachricht (`cogs/welcome.py`)
- `on_member_join` postet ein Embed in den konfigurierten Channel.
- Slash-Command `/set-welcome channel:<#channel>` (Berechtigung `manage_guild`).
- Slash-Command `/test-welcome` zum Testen ohne echten Join.
- Config liegt in `guild_config.welcome_channel_id`.

### 2. AutoRole (`cogs/autorole.py`)
- `on_member_join` vergibt die konfigurierte Rolle automatisch.
- Slash-Command `/set-autorole role:<@rolle>` (Berechtigung `manage_guild`).
- Schutz eingebaut: `@everyone` wird abgelehnt, ebenso Rollen über/gleich der höchsten Bot-Rolle
  (sonst würde `member.add_roles()` zur Laufzeit mit `discord.Forbidden` fehlschlagen).
- Config liegt in `guild_config.auto_role_id`.

### 3. Interne Bot-API (`cogs/internal_api.py`)
- Kleiner `aiohttp`-Webserver, der **innerhalb des Cogs** per `cog_load()`/`cog_unload()`
  gestartet/gestoppt wird (läuft also automatisch mit, sobald der Bot startet).
- Hört auf `BOT_API_HOST:BOT_API_PORT` (Default `0.0.0.0:8080`), nur per
  `Authorization: Bearer <INTERNAL_API_SECRET>` erreichbar.
- Einziger Endpunkt: `POST /api/send-embed` mit JSON `{channel_id, title, description, color}`.
- Da Bot und Dashboard im selben Container als zwei Prozesse laufen, erreicht das
  Dashboard diese API einfach über `http://127.0.0.1:8080` (kein Docker-Netzwerk nötig,
  das war nur in einer früheren Zwischenversion mit zwei Containern relevant).
- **Fehlt bisher:** keine Prüfung, dass der angefragte Channel wirklich zu einem Server
  gehört, auf dem der anfragende Dashboard-User Admin ist (siehe "Offene Punkte" unten).

### 4. Dashboard (`dashboard/app.py`)
- Discord-OAuth2-Login via `requests_oauthlib.OAuth2Session` (Scopes `identify`, `guilds`).
- `/dashboard`: listet Server, auf denen der eingeloggte User Owner ist oder das
  `ADMINISTRATOR`-Bit (`0x8`) in der Permission-Bitmaske gesetzt hat.
- `/embed-generator`: Formular (Kanal-ID, Titel, Beschreibung, Farbe), schickt die Daten
  per `requests.post()` an die interne Bot-API.
- `/server/<guild_id>`: Server-Verwaltungsseite (siehe Punkt 6 unten). Der "Verwalten"-Button
  auf jeder Server-Karte in `dashboard.html` verlinkt hierhin.
- Tailwind CSS über CDN, dunkles Theme im MEE6-Stil.

### 6. Server-Verwaltungsseite (`/server/<guild_id>`)
- `get_admin_guild_or_none()` prüft bei **jedem** Request neu über die OAuth2-Session, ob der
  eingeloggte User auf dieser Guild-ID Owner/Admin ist (kein Caching in der Session, Discord
  ist die Quelle der Wahrheit für die Permission-Bitmaske) — sonst `403` (`server.html` mit
  `error="access"`).
- Danach wird die Bot-API gefragt (`GET /api/guilds/<id>`, `/channels`, `/roles`, `/config`),
  ob der Bot überhaupt auf dem Server ist. Wenn nicht: `error="not_in_guild"`. Wenn die
  Bot-API nicht erreichbar ist: `error="unreachable"`.
- Zwei Formulare (Willkommens-Channel, AutoRole) posten an `/server/<guild_id>/welcome-channel`
  bzw. `/server/<guild_id>/autorole`, die jeweils erneut die Admin-Prüfung machen und dann die
  neuen Bot-API-Endpunkte aufrufen (siehe unten) statt die DB direkt zu beschreiben — damit
  bleibt die Validierungslogik (Rolle unterhalb der höchsten Bot-Rolle, Channel gehört zur
  Guild) an einer Stelle (`cogs/internal_api.py`), nicht doppelt in Flask.
- **Neue Bot-API-Endpunkte** in `cogs/internal_api.py`, alle wie `/api/send-embed` per
  Bearer-Token geschützt und 404, falls der Bot nicht auf der angefragten Guild ist:
  - `GET /api/guilds/<id>` — Guild-Infos (Existenzcheck)
  - `GET /api/guilds/<id>/channels` — alle Textchannels
  - `GET /api/guilds/<id>/roles` — Rollen, die automatisch vergeben werden können (ohne
    `@everyone`, ohne Rollen auf/über der höchsten Bot-Rolle — gleiche Regel wie in
    `/set-autorole`)
  - `GET /api/guilds/<id>/config` — aktuell gespeicherte `welcome_channel_id`/`auto_role_id`
  - `POST /api/guilds/<id>/welcome-channel` `{channel_id}` — validiert, dass der Channel zur
    Guild gehört, dann `db.set_welcome_channel()`
  - `POST /api/guilds/<id>/autorole` `{role_id}` — validiert Guild-Zugehörigkeit + die gleiche
    Höhen-Regel, dann `db.set_auto_role()`
- **Bekannte Lücke:** Es gibt (wie bei den Slash-Commands) keine Möglichkeit, eine einmal
  gesetzte Config wieder auf "nichts" zurückzusetzen — die Dropdowns bieten eine
  "— nichts ausgewählt —"-Option an, aber ein Submit mit leerem Wert schlägt mit einer
  Fehlermeldung fehl statt die Spalte auf `NULL` zu setzen. Falls gewünscht: eigene
  `unset_welcome_channel()`/`unset_auto_role()`-Funktionen in `database/db.py` ergänzen und
  die Bot-API-Endpunkte einen leeren Wert als "löschen" behandeln lassen.

### 5. Datenbank (`database/db.py`)
- Eine Tabelle `guild_config (guild_id PK, welcome_channel_id, auto_role_id)`.
- `init_db()` erstellt die Tabelle und migriert fehlende Spalten automatisch
  (`_column_exists` + `ALTER TABLE ... ADD COLUMN`), damit bestehende DB-Dateien beim
  Hinzufügen neuer Spalten nicht brechen. Bei künftigen neuen Spalten dieses Muster
  weiterführen.
- Pfad: `DB_PATH = os.environ.get("DB_PATH", "/app/data/database.db")`.

## Umgebungsvariablen

Alle Werte werden strikt per `os.environ.get()` gelesen, **keine Secrets sind im Code
hartkodiert**. `load_dotenv()` ist nur ein optionaler Komfort für lokale Entwicklung
(`.env`-Datei vorhanden und lokal ausgefüllt, ist in `.gitignore`).

**Pflicht** (Programm wirft `RuntimeError` beim Start, wenn diese fehlen):
- `DISCORD_TOKEN` (bot.py)
- `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI` (dashboard/app.py)
- `FLASK_SECRET_KEY` (dashboard/app.py)
- `INTERNAL_API_SECRET` (dashboard/app.py **und** cogs/internal_api.py — muss in beiden
  Prozessen identisch sein, da es dasselbe Secret für die interne API ist)

**Optional**, mit sinnvollen Defaults im Code:
- `BOT_API_HOST` (`0.0.0.0`), `BOT_API_PORT` (`8080`), `BOT_API_URL` (`http://127.0.0.1:8080`)
- `FLASK_PORT` (`5000`)
- `DB_PATH` (`/app/data/database.db`)
- `OAUTH_INSECURE_TRANSPORT` (`1` = http erlaubt für OAuth-Redirect; auf `0` setzen, sobald
  ein HTTPS-Reverse-Proxy vorgeschaltet ist)

Siehe `.env.example` für die vollständige, kommentierte Liste.

## Deployment

Es gibt **zwei parallele Wege**, die beide im Repo liegen:

1. **`docker-compose.yml`** — generisches Referenz-Setup für `docker compose up`
   (lokal oder auf jedem Docker-Host), mountet `./data:/app/data`.
2. **`deploy.sh`** — das tatsächlich für den Unraid-Server genutzte Skript. Es läuft
   lokal auf dem Entwickler-Laptop im LAN, baut das Image **auf dem Unraid-Host selbst**
   (per SSH) und startet den Container dort direkt mit `docker run` (nicht über Compose).
   - Zielserver: `192.168.178.41` (User `root`, Default in `deploy.sh`, per Env-Var
     `UNRAID_HOST`/`UNRAID_USER` überschreibbar)
   - Remote-Verzeichnis: `/mnt/user/appdata/discord-bot-dashboard`
   - Host-Port: `5001` → Container-Port `5000` (Default, per `HOST_PORT` überschreibbar)
   - Container-Name/Image: `discord-bot-dashboard`
   - Das Skript bricht kontrolliert ab, wenn `.env` fehlt, noch Platzhalterwerte enthält,
     oder der Zielport bereits von einem anderen Container belegt ist.
   - `data/` auf dem Unraid-Host bleibt bei jedem Redeploy unangetastet (explizit von der
     Dateiübertragung ausgeschlossen).

**Wichtig:** Falls an der Deploy-Strategie weitergearbeitet wird, zuerst klären, ob
`docker-compose.yml` noch gebraucht wird oder ob `deploy.sh` der einzige relevante Weg
ist — aktuell sind beide im Repo, aber nur `deploy.sh` spiegelt den echten Produktivpfad
wider (unterschiedliche Container-Namen/Ports zwischen beiden Dateien beachten!).

## Lokal testen (ohne Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # dann ausfüllen
python bot.py          # Terminal 1
python dashboard/app.py  # Terminal 2
```

Für Syntax-/Smoke-Checks ohne echten Discord-Token wurden in der bisherigen Entwicklung
u.a. folgende Muster verwendet (kein fertiges Testsuite vorhanden, nur Ad-hoc-Checks):
- `python -I -m py_compile <dateien>` für Syntaxfehler.
- Cogs isoliert laden (`bot.load_extension(...)`) und `bot.tree.get_commands()` prüfen,
  ohne `bot.run()`/echten Login aufzurufen.
- Interne API mit einem Fake-`bot`-Objekt (`get_channel()` gemockt) über `aiohttp.ClientSession`
  gegen `cogs/internal_api.py` testen (Auth, Validierung, Erfolgsfall).
- Dashboard-Route `/embed-generator` end-to-end gegen eine laufende Fake-Bot-API testen
  (Flask-Test-Client + echtem HTTP-Request an die interne API).

Es gibt noch kein automatisiertes Test-Framework (pytest o.ä.) — falls gewünscht, wäre das
ein sinnvoller nächster Schritt, um diese Ad-hoc-Checks dauerhaft nutzbar zu machen.

## Offene Punkte / bekannte Lücken

- **Kein XP/Level-System.** Wurde in einer frühen Phase bewusst zurückgestellt
  (Nutzerentscheidung), ist aber ein klassisches MEE6-Feature und naheliegende Erweiterung
  als neues Cog (`cogs/leveling.py` + eigene Tabelle/Spalten in `database/db.py`).
- **Embed-Generator prüft weiterhin nicht**, ob die eingegebene Kanal-ID tatsächlich zu einem
  Server gehört, auf dem der eingeloggte User Admin-Rechte hat — nur der Login an sich wird
  geprüft. Der dafür nötige Bot-API-Endpunkt (`GET /api/guilds/<id>/channels`) existiert jetzt
  bereits (siehe Punkt 6, wurde für die Server-Verwaltungsseite gebaut) — der Embed-Generator
  müsste nur noch umgestellt werden: Kanal-ID-Textfeld durch ein Dropdown aus
  `/api/guilds/<guild_id>/channels` ersetzen (Guild-ID z. B. als Pfadparameter wie bei
  `/server/<guild_id>`), statt eine freie Kanal-ID anzunehmen.
- **Kein Zurücksetzen von Willkommens-Channel/AutoRole auf "nichts"** über Dashboard oder
  Slash-Commands möglich (siehe Punkt 6 oben für Details).
- **Keine Tests/CI**, kein Git-Repo bisher initialisiert.
- Flask läuft mit dem eingebauten Dev-Server (`app.run(...)`), nicht mit einem
  Production-WSGI-Server wie gunicorn/waitress — für ein Homelab-Setup bewusst so belassen,
  für mehr Last wäre ein Wechsel sinnvoll.

## Wichtige Design-Entscheidungen (Begründung, damit nichts versehentlich rückgängig gemacht wird)

- **Interne API statt DB-Queue** für die Dashboard→Bot-Kommunikation gewählt, weil Embeds
  sofort gesendet werden sollen statt auf ein Polling-Intervall zu warten, und `aiohttp`
  ohnehin durch discord.py mitkommt (keine neue Abhängigkeit).
- **Zwei Prozesse in einem Container** (statt zwei Containern) war eine explizite
  Nutzeranforderung für Unraid, um nur einen Container verwalten zu müssen. `start.sh`
  sorgt dafür, dass beim Absturz eines der beiden Prozesse der gesamte Container stirbt
  (`wait -n`), damit Docker/Unraid ihn per Restart-Policy sauber neu startet.
- **Alle Secrets sind Pflicht-Env-Vars ohne Code-Defaults** (`require_env()`-Pattern in
  `dashboard/app.py`), explizit um keine unsicheren Default-Secrets im Image zu haben.
