# Discord MEE6 Bot

Ein selbst gehosteter Discord-Bot (discord.py, Slash-Commands) mit Web-Dashboard
(Flask + Discord-OAuth2), der schrittweise Kernfunktionen von MEE6 nachbildet. Bot und
Dashboard laufen **in einem einzigen Docker-Container** als zwei Prozesse.

## Features

- **Willkommensnachricht** — Embed beim Beitritt in einem konfigurierbaren Channel
  (`/set-welcome`, `/test-welcome`).
- **AutoRole** — automatische Rollenvergabe bei Beitritt (`/set-autorole`).
- **AutoMod** — löscht Discord-Einladungslinks und erkennt Nachrichten-Spam (löscht die
  Nachrichten, verwarnt per DM), protokolliert beides in einem Mod-Log-Channel
  (`/set-modlog`).
- **TempVoice** — erstellt pro Nutzer automatisch einen temporären Sprachkanal beim
  Betreten eines Ersteller-Kanals und löscht ihn wieder, sobald er leer ist
  (`/set-tempvoice`).
- **Web-Dashboard** — Discord-OAuth2-Login, Übersicht aller Server mit Adminrechten, pro
  Server eine Verwaltungsseite für Willkommens-Channel, AutoRole und einen
  Embed-Generator (Author/Footer/Thumbnail/Bild-Upload, dynamische Felder, Discord-Live-Vorschau).

## Architektur

```
Discord-host/
├── bot.py                        # Discord-Bot-Entrypoint, lädt alle Cogs automatisch
├── cogs/
│   ├── welcome.py                 # Willkommensnachricht
│   ├── autorole.py                # Automatische Rollenvergabe
│   ├── automod.py                 # Einladungslink-Filter + Anti-Spam
│   ├── tempvoice.py                # Temporäre Sprachkanäle
│   └── internal_api.py            # Interne aiohttp-API für Bot<->Dashboard-Kommunikation
├── database/
│   └── db.py                      # Gemeinsame SQLite-DB (aiosqlite)
├── dashboard/
│   ├── app.py                     # Flask-App: OAuth2-Login, Server-Verwaltung, Embed-Generator
│   └── templates/
├── Dockerfile                     # Ein Image für Bot + Dashboard
├── start.sh                       # Startet beide Prozesse im Container
├── docker-compose.yml             # Lokales Referenz-Setup
├── deploy.sh                      # Deploy-Skript (SSH + docker run auf Unraid)
├── requirements.txt
└── .env.example                   # Vorlage für alle benötigten Umgebungsvariablen
```

Ausführliche Hintergründe (Design-Entscheidungen, offene Punkte, Testmuster) stehen in
[`uebergabe.md`](uebergabe.md).

## Lokal einrichten

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # dann ausfüllen (Bot-Token, OAuth2-Client-ID/-Secret, Secrets)
python bot.py          # Terminal 1
python dashboard/app.py  # Terminal 2
```

Pflicht-Umgebungsvariablen (siehe [`.env.example`](.env.example) für die vollständige
Liste): `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`,
`DISCORD_REDIRECT_URI`, `FLASK_SECRET_KEY`, `INTERNAL_API_SECRET` (muss bei Bot und
Dashboard identisch sein).

## Deployment

Produktiv wird per [`deploy.sh`](deploy.sh) auf einen Unraid-Server deployt: baut das
Docker-Image direkt auf dem Zielhost per SSH und startet den Container dort. Für ein
generisches Docker-Setup liegt außerdem eine [`docker-compose.yml`](docker-compose.yml)
als Referenz bei.

```bash
bash deploy.sh
```

Details zu Zielport, Remote-Verzeichnis und Umgebungsvariablen stehen in
[`uebergabe.md`](uebergabe.md#deployment).
