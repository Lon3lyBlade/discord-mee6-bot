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
- **Events & Kalender** — `/event create` postet ein Event-Embed mit Zusage/Absage/
  Vielleicht-Buttons (Live-Teilnehmerliste, persistent über Bot-Neustarts). Optional kann
  eine Ziel-Rolle angegeben werden, auf die der Bot den Channel beschränkt. Events lassen
  sich zusätzlich komplett im Dashboard erstellen, bearbeiten und absagen.
- **Musik** — plattformübergreifende Musikwiedergabe über einen Lavalink-Node
  (`/play`, `/skip`, `/queue`, `/stop`/`/leave`) von YouTube/YouTube Music, Spotify, Apple
  Music und SoundCloud. Benötigt einen separat laufenden Lavalink-Server, siehe
  [Musik-System (Lavalink)](#musik-system-lavalink) unten.
- **Level-System** — 15–25 zufällige XP pro Nachricht (60-Sekunden-Cooldown pro Nutzer),
  Level-Up-Embeds im Chat oder per DM, `/rank [user]` (Level, XP, Fortschrittsbalken) und
  `/leaderboard` (Top 10 der Guild). Lässt sich im Dashboard pro Server an-/abschalten,
  für einzelne Channels/Rollen ausschließen und die Level-Up-Benachrichtigung umstellen.
  Zusätzlich konfigurierbare Level-Rollen-Belohnungen (z. B. Level 20 → Rolle
  "Oberhaupt"): Bei Level-Aufstieg wird automatisch die Rolle der höchsten erreichten
  Stufe vergeben, niedrigere Stufen-Rollen werden entfernt.
- **Web-Dashboard** — Discord-OAuth2-Login, Übersicht aller Server mit Adminrechten, pro
  Server eigene Seiten für Einstellungen (Willkommens-Channel, AutoRole), TempVoice-Kanäle,
  Events/Kalender, Level-System (Leaderboard + Einstellungen) und einen Embed-Generator
  (Author/Footer/Thumbnail/Bild-Upload, dynamische Felder, Discord-Live-Vorschau).

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
│   ├── events.py                  # Event-/Kalendersystem mit RSVP-Buttons
│   ├── music.py                   # Musik-Slash-Commands (Wavelink + Lavalink)
│   ├── leveling.py                 # XP-/Level-System (/rank, /leaderboard)
│   ├── blackjack.py                # Blackjack mit Coin-Einsatz (/blackjack, /balance, /coins-leaderboard)
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
│       ├── events.html            # Event-/Kalender-Übersicht + Erstellen/Bearbeiten
│       ├── levels.html            # Leaderboard + Level-System-Einstellungen
│       └── embed_builder.html     # Embed-Generator
├── lavalink/
│   ├── application.yml.example    # Vorlage für die Lavalink-Konfiguration (Musik-System)
│   └── plugins/                   # Mount-Ziel für die von Lavalink geladenen Plugin-.jars
├── .github/workflows/
│   └── docker-publish.yml         # Baut + pusht das Docker-Image nach ghcr.io bei Push auf master
├── Dockerfile                     # Ein Image für Bot + Dashboard
├── start.sh                       # Startet beide Prozesse im Container
├── docker-compose.yml             # Lokales Referenz-Setup
├── deploy.sh                      # Baut das Image auf Unraid und erstellt den Container neu
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

## Musik-System (Lavalink)

Das Musik-Feature (`cogs/music.py`) nutzt [Wavelink](https://github.com/PythonistaGuild/Wavelink)
als Client für einen separat laufenden [Lavalink](https://github.com/lavalink-devs/Lavalink)-
Node. Lavalink ist eine eigenständige Java-Anwendung und läuft daher **immer in einem
eigenen Container/Prozess**, nicht im Python-Bot-Container mit.

- **Lokal / generischer Docker-Host:** `docker-compose.yml` enthält bereits einen
  `lavalink`-Service. Einmalig `cp lavalink/application.yml.example
  lavalink/application.yml` ausführen und darin das Passwort (identisch zu
  `LAVALINK_SERVER_PASSWORD` in der `.env`) sowie optional Spotify-Client-ID/-Secret
  eintragen (siehe Kommentare in der Datei), dann `docker compose up -d`.
- **Unraid:** Da der produktive Bot-Container dort als eigenes, von Unraid verwaltetes
  Template läuft (kein `docker-compose`, siehe Abschnitt "Deployment" unten), muss
  Lavalink dort als **zweiter, separater Unraid-Container** angelegt werden (z. B. über
  das "Lavalink"-Template aus Community Apps, oder manuell mit dem Image
  `ghcr.io/lavalink-devs/lavalink:4`, Port 2333, `application.yml` als Pfad-Mapping). Im
  Bot-Container-Template dann `LAVALINK_HOST`/`LAVALINK_PORT` auf die IP/den Hostnamen
  dieses zweiten Containers setzen.
- Ohne gesetztes `LAVALINK_SERVER_PASSWORD` bzw. ohne erreichbaren Lavalink-Node bleiben
  die Musik-Slash-Commands einfach deaktiviert (freundliche Fehlermeldung statt Crash) —
  der restliche Bot startet unabhängig davon ganz normal.
- Spotify- und Apple-Music-Links/-Suchen laufen über das
  [LavaSrc](https://github.com/topi314/LavaSrc)-Plugin: Es löst dort nur Metadaten auf,
  die eigentliche Wiedergabe erfolgt über die passende YouTube-Quelle. Dafür wird (kostenlos)
  eine Spotify-App unter https://developer.spotify.com/dashboard benötigt; Apple Music
  braucht kein eigenes Secret.

## Deployment

Das Image wird **auf dem Unraid-Server selbst gebaut**, kostenlos und ohne GitHub Actions oder
Registry. Auf dem Laptop (im LAN) im Projektordner:

```bash
bash deploy.sh
```

[`deploy.sh`](deploy.sh) kopiert den Projektordner per SSH nach Unraid, baut dort das Image und
erstellt den bestehenden Container mit seiner **eigenen Konfiguration** neu (Variablen, Ports,
Volumes, Labels, Restart-Policy werden aus dem laufenden Container übernommen). Secrets und die
Daten unter `/app/data` bleiben erhalten; startet der neue Container nicht, wird der alte
automatisch wiederhergestellt. Eine lokale `.env` ist nicht nötig. Abweichende Werte per
Umgebungsvariable, z.B. `CONTAINER=meinbot UNRAID_HOST=192.168.x.x bash deploy.sh`
(Standard: `tower.local`, Container `discord-bot-dashboard`).

Der Container muss einmalig existieren (z.B. über Unraids "Add Container"). Unraids
"Update Ready"-Anzeige entfällt, da es kein Registry-Image mehr gibt. Der Workflow
[`docker-publish.yml`](.github/workflows/docker-publish.yml) läuft nur noch manuell.
Für ein generisches Docker-Setup ohne Unraid liegt eine [`docker-compose.yml`](docker-compose.yml) bei.

Details zu Zielport, Remote-Verzeichnis und Umgebungsvariablen stehen in
[`uebergabe.md`](uebergabe.md#deployment).
