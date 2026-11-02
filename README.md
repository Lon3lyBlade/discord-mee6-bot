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
- **Moderation** — Slash-Commands für Kick, Ban, Unban, Timeout/Untimeout, Warn (inkl.
  persistenter Verwarnungs-Historie) und Clear/Purge von Nachrichten. Alle Aktionen werden
  im Mod-Log-Channel protokolliert; die Verwarnungen lassen sich zusätzlich im Dashboard
  einsehen und löschen.
- **TempVoice** — erstellt pro Nutzer automatisch einen temporären Sprachkanal beim
  Betreten eines beliebigen konfigurierten Ersteller-Kanals (in dessen eigener Kategorie)
  und löscht ihn wieder, sobald er leer ist. Konfiguration läuft ausschließlich über das
  Dashboard (beliebig viele Ersteller-Kanäle per Checkbox auswählbar).
- **Web-Dashboard** — Discord-OAuth2-Login, Übersicht aller Server mit Adminrechten, pro
  Server eigene Seiten für Einstellungen (Willkommens-Channel, AutoRole), TempVoice-Kanäle
  und einen Embed-Generator (Author/Footer/Thumbnail/Bild-Upload, dynamische Felder,
  Discord-Live-Vorschau).

## Architektur

```
Discord-host/
├── bot.py                        # Discord-Bot-Entrypoint, lädt alle Cogs automatisch
├── cogs/
│   ├── welcome.py                 # Willkommensnachricht
│   ├── autorole.py                # Automatische Rollenvergabe
│   ├── automod.py                 # Einladungslink-Filter + Anti-Spam
│   ├── moderation.py               # Kick/Ban/Timeout/Warn/Clear-Slash-Commands
│   ├── tempvoice.py                # Temporäre Sprachkanäle
│   └── internal_api.py            # Interne aiohttp-API für Bot<->Dashboard-Kommunikation
├── database/
│   └── db.py                      # Gemeinsame SQLite-DB (aiosqlite)
├── dashboard/
│   ├── app.py                     # Flask-App: OAuth2-Login, Server-Verwaltung, Embed-Generator
│   └── templates/
│       ├── index.html             # Login-Seite
│       ├── dashboard.html         # Server-Übersicht (Admin-Guilds des Users)
│       ├── _sidebar.html          # Sidebar inkl. Server-Switcher
│       ├── _guild_layout.html     # Gemeinsames Layout für die Server-Unterseiten
│       ├── settings.html          # Willkommens-Channel + AutoRole
│       ├── moderation.html         # Mod-Log-Channel + Verwarnungs-Übersicht
│       ├── tempvoice.html         # TempVoice-Kanalauswahl
│       └── embed_builder.html     # Embed-Generator
├── .github/workflows/
│   └── docker-publish.yml         # Baut + pusht das Docker-Image nach ghcr.io bei Push auf master
├── Dockerfile                     # Ein Image für Bot + Dashboard
├── start.sh                       # Startet beide Prozesse im Container
├── docker-compose.yml             # Lokales Referenz-Setup
├── deploy.sh                      # Zieht das aktuelle Image von ghcr.io und startet den Container auf Unraid neu
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

Das Docker-Image wird nicht mehr lokal auf dem Zielhost gebaut: Ein GitHub-Actions-Workflow
([`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)) baut es bei
jedem Push auf `master` und veröffentlicht es in der GitHub Container Registry als
`ghcr.io/lon3lyblade/discord-mee6-bot`. Unraids Docker-Tab erkennt darüber von sich aus neue
Versionen ("Update Ready"-Button).

[`deploy.sh`](deploy.sh) zieht dieses Image per SSH auf einen Unraid-Server und startet den
Container dort neu — praktisch, wenn man nicht auf Unraids automatischen Update-Check warten
will. Für ein generisches Docker-Setup liegt außerdem eine
[`docker-compose.yml`](docker-compose.yml) als Referenz bei.

```bash
bash deploy.sh
```

Einmalig nötig, da das GitHub-Package privat ist: auf dem Zielhost per
`docker login ghcr.io -u <dein-github-username>` mit einem Personal Access Token
(Scope `read:packages`) anmelden.

Details zu Zielport, Remote-Verzeichnis und Umgebungsvariablen stehen in
[`uebergabe.md`](uebergabe.md#deployment).
