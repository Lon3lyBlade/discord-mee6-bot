# Übergabe: Discord-Bot + Web-Dashboard (MEE6-Klon)

Stand: 2026-11-03. Dieses Dokument richtet sich an eine andere KI/Entwicklungsinstanz,
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
│   ├── automod.py                 # Einladungslink-Filter + Anti-Spam
│   ├── moderation.py               # Kick/Ban/Timeout/Warn/Clear-Slash-Commands
│   ├── tempvoice.py                # Temporäre Sprachkanäle
│   ├── music.py                   # Musik-Slash-Commands (Wavelink + Lavalink)
│   ├── leveling.py                 # XP-/Level-System (/rank, /leaderboard)
│   └── internal_api.py            # Interne aiohttp-API für Bot<->Dashboard-Kommunikation
├── database/
│   └── db.py                      # Gemeinsame SQLite-DB (aiosqlite)
├── lavalink/
│   ├── application.yml.example    # Vorlage für die Lavalink-Konfiguration
│   └── plugins/                   # Mount-Ziel für von Lavalink geladene Plugin-.jars
├── dashboard/
│   ├── app.py                     # Flask-App: OAuth2-Login, Server-Verwaltung, Embed-Generator
│   └── templates/
│       ├── index.html             # Login-Seite
│       ├── dashboard.html         # Server-Übersicht (Admin-Guilds des eingeloggten Users)
│       ├── _sidebar.html          # Sidebar inkl. Server-Switcher
│       ├── _guild_layout.html     # Gemeinsames Layout für die Server-Unterseiten
│       ├── settings.html          # Willkommens-Channel + AutoRole
│       ├── moderation.html         # Mod-Log-Channel + Verwarnungs-Übersicht
│       ├── tempvoice.html         # TempVoice-Kanalauswahl
│       ├── levels.html            # Leaderboard + Level-System-Einstellungen
│       └── embed_builder.html     # Embed-Generator
├── .github/workflows/
│   └── docker-publish.yml         # Baut + pusht das Docker-Image nach ghcr.io bei Push auf master
├── Dockerfile                     # Ein Image für Bot + Dashboard
├── start.sh                       # Startet beide Prozesse im Container, leitet SIGTERM weiter
├── docker-compose.yml             # Generisches Referenz-Setup für Nicht-Unraid-Hosts
├── deploy.sh                      # Veraltetes Deploy-Skript, siehe Abschnitt "Deployment" unten
├── requirements.txt
├── .env.example                   # Vorlage, welche Env-Vars nötig sind
└── .env                           # Lokal ausgefüllt, NICHT eingecheckt (siehe .gitignore)
```

Git-Repo ist initialisiert, Remote ist ein **privates** GitHub-Repo
(`github.com/Lon3lyBlade/discord-mee6-bot`), Branch `master`.

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
- Alle Endpunkte sind guild-gebunden (`/api/guilds/<guild_id>/...`) und geben `404`
  zurück, falls der Bot nicht auf dieser Guild ist. Der Channel für `send-embed` wird
  dabei gezielt über `guild.get_channel()` aufgelöst statt global über den Bot, damit ein
  Dashboard-User kein Embed in einen Channel auf einem fremden Server schicken kann, auf
  dem er keine Adminrechte hat (war früher eine offene Lücke, siehe Punkt 6 unten).
- `POST .../send-embed` ist **immer Multipart** (`await request.post()`), nie reines JSON —
  auch ohne Bild-Upload. Alle Skalarwerte (Titel, Beschreibung, Farbe, Author/Footer/
  Thumbnail, Timestamp-Flag, Felder-Liste, `channel_id`) stecken gebündelt in einem
  JSON-String im Textpart `fields_json`; ein optionaler Bild-Upload kommt als echter
  Datei-Part `image_file` dazu. **Fallstrick, falls daran weitergearbeitet wird:** `fields_json`
  darf **keinen** expliziten Content-Type (z. B. `"application/json"`) im Multipart-Part
  bekommen — aiohttps `request.post()` liefert einen Textpart nur dann als `str` zurück,
  wenn der Part keinen oder einen `text/*`-Content-Type hat; mit `application/json` kommt
  stattdessen ein `bytearray` an. `dashboard/app.py` schickt das Feld deshalb bewusst als
  2-Tupel `(None, json.dumps(payload))` ohne Content-Type; `_handle_send_embed` dekodiert
  zusätzlich defensiv ein `bytes`/`bytearray`-Ergebnis, falls doch mal ein Client mit
  explizitem Content-Type postet. `client_max_size` von `web.Application(...)` ist von
  aiohttps Default (1 MiB) auf 10 MB hochgesetzt (siehe `MAX_IMAGE_SIZE` unten), Flask setzt
  passend `MAX_CONTENT_LENGTH = 9 MB`.
- Da Bot und Dashboard im selben Container als zwei Prozesse laufen, erreicht das
  Dashboard diese API einfach über `http://127.0.0.1:8080` (kein Docker-Netzwerk nötig,
  das war nur in einer früheren Zwischenversion mit zwei Containern relevant).

### 4. Dashboard (`dashboard/app.py`)
- Discord-OAuth2-Login via `requests_oauthlib.OAuth2Session` (Scopes `identify`, `guilds`).
- `/dashboard`: listet Server, auf denen der eingeloggte User Owner ist oder das
  `ADMINISTRATOR`-Bit (`0x8`) in der Permission-Bitmaske gesetzt hat.
- `/server/<guild_id>`: Server-Verwaltungsseite (siehe Punkt 6 unten) — enthält auch den
  Embed-Generator. Der "Verwalten"-Button auf jeder Server-Karte in `dashboard.html`
  verlinkt hierhin. Es gibt **keinen** eigenständigen `/embed-generator` mehr, der wurde
  in diese Seite integriert (siehe Punkt 6).
- Tailwind CSS über CDN, dunkles Theme im MEE6-Stil.

### 6. Server-Verwaltungsseite (`/server/<guild_id>`)
- `get_admin_guild_or_none()` prüft bei **jedem** Request neu über die OAuth2-Session, ob der
  eingeloggte User auf dieser Guild-ID Owner/Admin ist (kein Caching in der Session, Discord
  ist die Quelle der Wahrheit für die Permission-Bitmaske) — sonst `403` (`server.html` mit
  `error="access"`).
- Danach wird die Bot-API gefragt (`GET /api/guilds/<id>`, `/channels`, `/roles`, `/config`),
  ob der Bot überhaupt auf dem Server ist. Wenn nicht: `error="not_in_guild"`. Wenn die
  Bot-API nicht erreichbar ist: `error="unreachable"`.
- Drei Formulare (Willkommens-Channel, AutoRole, Embed-Generator) posten an
  `/server/<guild_id>/welcome-channel`, `/server/<guild_id>/autorole` bzw.
  `/server/<guild_id>/send-embed`, die jeweils erneut die Admin-Prüfung machen und dann die
  Bot-API-Endpunkte aufrufen (siehe unten) statt die DB direkt zu beschreiben oder den Bot
  direkt anzusprechen — damit bleibt die Validierungslogik (Rolle unterhalb der höchsten
  Bot-Rolle, Channel gehört zur Guild) an einer Stelle (`cogs/internal_api.py`), nicht doppelt
  in Flask.
- Der Embed-Generator ist **kein eigener Menüpunkt mehr**, sondern Teil von
  `server.html` — Kanal wird per `<select>` aus den schon geladenen `channels` der Guild
  gewählt statt eine Kanal-ID frei einzutippen (Nutzeranforderung: Channel-Auswahl statt
  ID-Eingabe, zugleich schließt das die alte Autorisierungslücke).
- **Embed-Generator-Upgrade:** zusätzlich zu Titel/Beschreibung/Farbe/Channel jetzt auch
  Titel-Link (`url`), Timestamp-Checkbox, Author (Name/URL/Icon), Footer (Text/Icon),
  Thumbnail-URL, Bild per URL **oder** per Datei-Upload (Upload hat Vorrang, siehe unten),
  sowie beliebig viele dynamische Felder (Name/Value/Inline). Die Felder-Liste wird im
  Formular **nicht** als Hidden-JSON gepflegt, sondern als parallele Arrays
  `field_name[]`/`field_value[]`/`field_inline[]` — `inline` ist bewusst ein `<select>`
  (`"true"`/`"false"`) statt einer Checkbox, weil unchecked Checkboxen beim Submit fehlen
  würden und die drei Arrays sonst aus dem Index liefen. Die Vorschau in `server.html` ist
  jetzt eine Discord-ähnliche Struktur (Author-Zeile, Titel als Link, Felder-Grid mit
  `flex: 1 1 30%` für inline bzw. `1 1 100%` für volle Breite, Bild, Thumbnail oben rechts,
  Footer mit Zeitstempel), inkl. Live-Vorschau eines hochgeladenen Bildes über
  `URL.createObjectURL()`.
- **Discord-Embed-Limits** werden in `_handle_send_embed` vorab geprüft (max. 25 Felder,
  Titel ≤256, Beschreibung ≤4096, Feldname ≤256, Feldwert ≤1024, Footer-Text ≤2048,
  Autor-Name ≤256, Gesamtlänge `len(embed) ≤ 6000`) und liefern bei Verstoß eine klare
  deutsche 400-Fehlermeldung statt einer rohen `discord.HTTPException` von Discords API.
- **Bild-Upload:** Cap 8 MB (`MAX_IMAGE_SIZE`), erlaubte Content-Types PNG/JPEG/GIF/WebP.
  Das Bild wird als `discord.File` mitgeschickt und per `embed.set_image(url=
  f"attachment://{filename}")` ins Embed eingebunden (Discords Standard-Pattern für
  Attachment-Bilder in Embeds).
- **Bot-API-Endpunkte** in `cogs/internal_api.py`, alle guild-gebunden
  (`/api/guilds/<id>/...`), per Bearer-Token geschützt und 404, falls der Bot nicht auf der
  angefragten Guild ist:
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
  - `POST /api/guilds/<id>/send-embed` — Multipart (siehe oben), validiert Guild-Zugehörigkeit
    des Channels + Discord-Limits, baut das volle Embed (Author/Footer/Thumbnail/Bild/Felder/
    Timestamp) und sendet es, optional mit Datei-Attachment
- **Bekannte Lücke:** Es gibt (wie bei den Slash-Commands) keine Möglichkeit, eine einmal
  gesetzte Config wieder auf "nichts" zurückzusetzen — die Dropdowns bieten eine
  "— nichts ausgewählt —"-Option an, aber ein Submit mit leerem Wert schlägt mit einer
  Fehlermeldung fehl statt die Spalte auf `NULL` zu setzen. Falls gewünscht: eigene
  `unset_welcome_channel()`/`unset_auto_role()`-Funktionen in `database/db.py` ergänzen und
  die Bot-API-Endpunkte einen leeren Wert als "löschen" behandeln lassen.

### 7. Moderation (`cogs/moderation.py`)
- Klassische manuelle Mod-Befehle, bewusst **getrennt von AutoMod** (`cogs/automod.py`
  bleibt für automatische Link-/Spam-Erkennung zuständig; `moderation.py` für Aktionen, die
  ein Mod gezielt auslöst):
  - `/kick`, `/ban` (mit `delete_message_days`), `/unban` (per User-ID, da gebannte Nutzer
    keine `Member`-Objekte mehr sind — daher `discord.Object(id=...)`)
  - `/timeout` / `/untimeout` — Dauer als String (`10m`, `2h`, `1d`, max. 28 Tage gemäß
    Discord-Limit), geparst von `parse_duration()` (reine Zahl = Minuten, Suffixe
    `s/m/h/d/w`)
  - `/warn`, `/warnings`, `/clearwarnings` — Verwarnungen werden persistent in der neuen
    `warnings`-Tabelle gespeichert (siehe Punkt 5), nicht nur als DM/Log-Eintrag
  - `/clear` — Bulk-Delete der letzten 1-100 Nachrichten im aktuellen Channel
    (`channel.purge()`)
- Alle Aktionen loggen ins **gleiche** `mod_log_channel_id` wie AutoMod (gemeinsame Config-
  Spalte, siehe Punkt 5) — `moderation.py` hat dafür eine eigene Kopie von
  `_get_log_channel()`/`_log()` (bewusst dupliziert statt einer gemeinsamen Hilfsdatei, da
  es nur zwei Stellen sind und beide Cogs sonst unabhängig bleiben sollen).
- Jede Aktion versucht zusätzlich eine DM an den betroffenen Nutzer (best-effort, `discord.
  Forbidden` wird verschluckt, gleiches Muster wie in `automod.py`/`welcome.py`).
- Berechtigungen sind granular nach Discord-Permission gewählt: `kick_members` für `/kick`,
  `ban_members` für `/ban`/`/unban`, `moderate_members` für `/timeout`/`/untimeout`/`/warn`/
  `/warnings`/`/clearwarnings`, `manage_messages` für `/clear`.
- **Bekannte Lücke:** Es gibt keine eigene Rollenhierarchie-Prüfung auf Höhe des
  *ausführenden* Mods (Discord prüft bei Bot-Aktionen nur die Rollenhöhe des **Bots**, nicht
  die des Slash-Command-Nutzers) — ein Mod mit `kick_members` könnte daher theoretisch
  jemanden kicken lassen, den er selbst in der Discord-UI nicht kicken dürfte, solange der
  Bot selbst die nötige Rollenhöhe hat. Für ein Homelab-Setup bewusst nicht weiter
  eingeschränkt; falls gewünscht, ließe sich das analog zur `_is_assignable_role()`-Logik in
  `internal_api.py`/`autorole.py` ergänzen.
- **Dashboard-Seite** `/server/<guild_id>/moderation` (`moderation.html`) zeigt/ändert den
  Mod-Log-Channel (gleiches `<select>`-Pattern wie Willkommens-Channel) und listet alle
  Verwarnungen des Servers mit Lösch-Button pro Zeile. Die eigentlichen Mod-Aktionen
  (Kick/Ban/Timeout/Warn) laufen bewusst **nur** über Slash-Commands in Discord (nicht übers
  Dashboard), damit Moderation dort passiert, wo das Fehlverhalten auftritt — das Dashboard
  dient hier nur der Konfiguration und der Einsicht/Aufräumen der Verwarnungs-Historie,
  analog zum Muster bei TempVoice (Dashboard konfiguriert, der Bot führt aus).
- Neue Bot-API-Endpunkte in `cogs/internal_api.py`: `POST .../mod-log-channel`,
  `GET .../warnings` (inkl. aufgelöster Anzeigenamen über `guild.get_member()`/
  `bot.get_user()`, Fallback `"Unbekannter Nutzer (<id>)"` falls der Nutzer nicht im Cache
  ist), `POST .../warnings/<id>/delete`. `GET .../config` liefert jetzt zusätzlich
  `mod_log_channel_id`.

### 8. AutoMod (`cogs/automod.py`)
- `on_message` filtert Nachrichten von Bots/außerhalb von Guilds sofort raus, dann zwei
  unabhängige Checks nacheinander (`_handle_link_filter`, dann `_handle_spam`):
  - **Einladungslink-Filter**: löscht Nachrichten, die `discord.gg/` enthalten, außer der
    Autor hat `administrator`. Loggt ins Mod-Log.
  - **Anti-Spam**: pro `(guild_id, user_id)` wird ein `collections.deque(maxlen=20)` der
    letzten Nachrichten im RAM gehalten (**kein DB-Storage, geht bei Bot-Neustart verloren**,
    bewusst so belassen — reiner Kurzzeit-Spam-Schutz). Mehr als `SPAM_MESSAGE_LIMIT` (5)
    Nachrichten innerhalb von `SPAM_INTERVAL_SECONDS` (3) → alle betroffenen Nachrichten
    werden gelöscht, der User bekommt eine Warn-DM (best-effort, `discord.Forbidden`
    verschluckt), Log-Eintrag.
- Nutzt die **gleiche** `mod_log_channel_id`-Spalte/den gleichen `_get_log_channel()`-Fallback
  (Channel-Suche nach dem Namen `"mod-log"`, falls nichts konfiguriert) wie `moderation.py` —
  dort bewusst dupliziert statt gemeinsamer Hilfsdatei (siehe Punkt 7).
- Slash-Command `/set-modlog channel:<#channel>` (Berechtigung `manage_guild`) — identischer
  Befehl wie in `moderation.py` beschrieben, beide Cogs teilen sich die eine Config-Spalte.
- **Kein Dashboard-Pendant** für AutoMod selbst (nur der Mod-Log-Channel ist über
  `moderation.html` einstellbar) — Schwellenwerte (`SPAM_MESSAGE_LIMIT`,
  `SPAM_INTERVAL_SECONDS`, `INVITE_LINK_MARKER`) sind aktuell Modulkonstanten, keine
  Pro-Server-Config. Falls gewünscht: eigene `guild_config`-Spalten + Dashboard-Formular
  analog zum TempVoice-Muster ergänzen.

### 9. TempVoice (`cogs/tempvoice.py`)
- `on_voice_state_update` reagiert auf zwei Fälle:
  - **Beitritt zu einem Ersteller-Kanal** (`after.channel.id` ist einer der konfigurierten
    `tempvoice_creator_channels`): erstellt einen neuen Sprachkanal `🔊 | {display_name}'s
    Lounge` **in derselben Kategorie wie der Ersteller-Kanal** (nicht in einer separat
    konfigurierten Kategorie — bewusste Vereinfachung, damit beliebig viele Ersteller-Kanäle
    ohne zusätzliche Kategorie-Zuordnung pro Kanal funktionieren), vergibt dem beitretenden
    Mitglied `manage_channels`+`move_members`-Overwrites auf dem neuen Kanal, verschiebt es
    per `member.move_to()` sofort hinein.
  - **Verlassen eines Kanals**: wird gelöscht, wenn er (a) selbst kein Ersteller-Kanal ist,
    (b) leer ist (`len(channel.members) == 0`) und (c) in derselben Kategorie wie **irgendein**
    aktuell konfigurierter Ersteller-Kanal liegt (`_is_managed_category()`, live über
    `guild.get_channel()` aufgelöst, nicht in der DB gespeichert) — verhindert, dass fremde
    leere Voice-Channels in anderen Kategorien versehentlich gelöscht werden.
- **Konfiguration läuft ausschließlich über das Dashboard** (`tempvoice.html`,
  Checkbox-Mehrfachauswahl aller Voice-Channels der Guild) — es gibt bewusst **keinen**
  Slash-Command mehr dafür (gab es in einer früheren Version, wurde entfernt, als die
  Mehrfachauswahl übers Dashboard kam, um nicht zwei potenziell widersprüchliche
  Config-Quellen zu haben).
- Bot-API-Endpunkte in `cogs/internal_api.py`: `GET /api/guilds/<id>/voice-channels` (alle
  Voice-Channels der Guild, für die Dashboard-Checkboxliste), `POST
  /api/guilds/<id>/tempvoice-channels` `{channel_ids: [...]}` (validiert, dass jede ID zur
  Guild gehört und ein `discord.VoiceChannel` ist, dann `db.set_tempvoice_creator_channels()`
  — ersetzt dabei die komplette Liste). `GET .../config` liefert zusätzlich
  `tempvoice_channel_ids` (Liste von Strings).

### 10. Musik (`cogs/music.py`)
- Nutzt [Wavelink v3](https://github.com/PythonistaGuild/Wavelink) als Client für einen
  separat laufenden **Lavalink**-Node (eigenständige Java-Anwendung, läuft bewusst in
  einem eigenen Container/Prozess, nicht im Python-Bot-Container mit — siehe
  Abschnitt "Deployment" unten für Unraid-spezifische Details).
- Slash-Commands: `/play query:<song/link>` (sucht automatisch oder verarbeitet direkte
  URLs von YouTube/YouTube Music/Spotify/Apple Music/SoundCloud; verbindet den Bot bei
  Bedarf automatisch mit dem Voice-Channel des aufrufenden Nutzers), `/skip`, `/queue`
  (Embed mit aktuellem Song + den nächsten bis zu 10 Songs), `/stop`/`/leave` (zwei
  identische Commands für denselben Zweck, wie vom Nutzer gewünscht).
- `MusicPlayer(wavelink.Player)` ist eine minimale Subklasse, die nur den Text-Channel
  für die "Jetzt läuft"-Nachricht merkt (`player.text_channel`); `on_wavelink_track_start`
  postet dort ein Embed mit Titel/Künstler/Cover/Dauer/Quelle, sobald ein Track startet.
  Die automatische Wiedergabe des nächsten Songs aus der Queue läuft komplett über
  Wavelinks eigenes `player.autoplay = wavelink.AutoPlayMode.partial` (zieht bei Songende
  selbstständig den nächsten Track aus `player.queue`) — es gibt bewusst **keinen**
  eigenen `on_wavelink_track_end`-Handler, der das nochmal manuell nachbauen würde.
  `AutoPlayMode.enabled` wurde bewusst **nicht** gewählt, da es bei leerer Queue
  zusätzlich eigenständig ähnliche Songs vorschlägt/einreiht — für dieses Projekt nicht
  gewünschtes Verhalten.
- Spotify- und Apple-Music-Links/-Suchen laufen über das
  [LavaSrc](https://github.com/topi314/LavaSrc)-Plugin auf dem Lavalink-Node: Es löst dort
  nur Metadaten auf (Titel/Künstler/ISRC), die eigentliche Audio-Wiedergabe erfolgt über
  eine passende YouTube-Quelle. Das native YouTube-Source von Lavalink ist seit v4
  entfernt (YouTube blockiert es serverseitig) — stattdessen das offizielle
  `youtube-source`-Plugin (`dev.lavalink.youtube:youtube-plugin`), SoundCloud läuft nativ.
  Beide Plugins werden über die `lavalink.plugins`-Liste in `application.yml` beim
  Lavalink-Start automatisch heruntergeladen (keine manuell gepflegten .jar-Dateien im
  Repo, `lavalink/plugins/` ist nur das Mount-Ziel dafür).
- **Fehlerbehandlung:** Ohne gesetztes `LAVALINK_SERVER_PASSWORD` loggt `cog_load()` nur
  eine Warnung und bricht ab — die Musik-Commands bleiben registriert, scheitern aber mit
  einer deutschen Fehlermeldung (`wavelink.Pool.nodes` ist dann leer, wird vor der Suche
  geprüft). **Wichtiger Fallstrick:** `wavelink.Pool.connect()` versucht bei nicht
  erreichbarem Node **unbegrenzt oft** mit Backoff erneut zu verbinden (siehe
  `Node._connect`/`Websocket.connect` im Wavelink-Quellcode) — ein direktes `await` davon
  in `cog_load()` würde bei einem noch nicht bereiten oder dauerhaft nicht erreichbaren
  Lavalink-Node (z. B. Java-Container, der langsamer startet als der Python-Bot)
  `setup_hook()` und damit den **kompletten Bot-Start** blockieren, da `bot.py` alle Cogs
  sequentiell lädt und danach erst die Slash-Commands synct. Deshalb läuft die Verbindung
  als `asyncio.create_task()` im Hintergrund (`self._connect_task`), `cog_unload()` bricht
  diesen Task ab und schließt den Pool. Beim Weiterarbeiten an diesem Cog **nicht**
  versehentlich zurück auf ein direktes `await wavelink.Pool.connect(...)` wechseln.
  Sonstige Fehler (ungültiger Link → `wavelink.LavalinkLoadException`, keine Ergebnisse,
  fehlende Berechtigung/Timeout beim Voice-Connect) werden pro Command einzeln abgefangen.
- Erfordert `discord.Intents.default()`s `voice_states` (nicht-privilegiert, bereits
  standardmäßig aktiv in `bot.py` — keine Änderung an den Intents nötig).
- **Kein Dashboard-Pendant.** Reine Slash-Command-Funktionalität, analog zu Moderation.
- **Bekannte Lücken:** kein `/pause`/`/resume`/`/volume`/`/nowplaying`, keine
  Berechtigungsprüfung (jeder Nutzer im Voice-Channel darf `/skip`/`/stop` ausführen),
  kein automatisches Verlassen des Kanals bei Inaktivität/leerem Voice-Channel. Bewusst
  nicht in Scope der ursprünglichen Anforderung, aber naheliegende Erweiterungen.

### 11. Level-System (`cogs/leveling.py`)
- `on_message` vergibt 15–25 zufällige XP pro Nachricht (ignoriert Bots und DMs), mit
  einem rein **im Speicher gehaltenen** 60-Sekunden-Cooldown pro `(guild_id, user_id)`
  (`self._last_xp_at`, kein DB-Zugriff fürs Cooldown-Tracking — geht bei Bot-Neustart
  verloren, das ist hier bewusst in Ordnung, da es nur Spam-Schutz ist, keine Nutzerdaten).
- Level-Formel: `level = int((xp / 100) ** 0.5)` (`db.compute_level()`), Umkehrung
  `xp_for_level(level) = level**2 * 100` liefert die minimale XP-Schwelle für ein Level —
  wird für den Fortschrittsbalken in `/rank` gebraucht.
- Pro Guild abschaltbar (`guild_config.xp_enabled`) sowie pro Channel/Rolle ausschließbar
  (`level_excluded_channels`/`level_excluded_roles`, siehe Datenbankabschnitt) — alles rein
  über das Dashboard konfigurierbar, keine eigenen Slash-Commands dafür.
- Bei Level-Aufstieg abhängig von `guild_config.levelup_notify_mode` (`chat` = Embed im
  Nachrichten-Channel, `dm` = Embed per DM, `disabled` = keine Nachricht).
- Slash-Commands: `/rank [user]` (Embed mit Level, XP, XP bis zum nächsten Level und
  Text-Fortschrittsbalken aus `█`/`░`), `/leaderboard` (Top 10 der Guild nach XP, mit
  Medaillen-Emojis für die ersten drei Plätze).
- **Dashboard-Pendant:** `/server/<guild_id>/levels` zeigt das komplette Leaderboard
  (alle Nutzer, nicht nur Top 10) und erlaubt An-/Abschalten der XP-Vergabe, die
  Level-Up-Benachrichtigung sowie ausgeschlossene Channels/Rollen. Läuft über neue
  Endpunkte in `cogs/internal_api.py` (`/api/guilds/<id>/levels`, `.../levels/config`,
  `.../levels/excluded-channels`, `.../levels/excluded-roles`).
- **Bekannte Lücken:** kein konfigurierbarer XP-Range oder Cooldown-Wert übers Dashboard
  (aktuell Konstanten im Cog), keine Level-Rollen-Belohnungen (z. B. automatische Rolle
  bei Level X) — naheliegende Erweiterung, aber nicht Teil der ursprünglichen Anforderung.

### 5. Datenbank (`database/db.py`)
- Eine Tabelle `guild_config (guild_id PK, welcome_channel_id, auto_role_id,
  mod_log_channel_id, xp_enabled, levelup_notify_mode)` für alle 1:1-Konfigurationswerte
  pro Server.
- `user_levels (guild_id, user_id, xp, level, PK(guild_id, user_id))` für die persistenten
  XP-/Level-Stände des Level-Systems (siehe Punkt 11), plus `level_excluded_channels` und
  `level_excluded_roles` (jeweils `guild_id, channel_id`/`role_id`, PK auf beiden Spalten)
  für die Dashboard-Ausschlusslisten — analog zum Muster von
  `tempvoice_creator_channels` unten (n:1-Daten, eigene Tabelle statt Spalte).
- `tempvoice_creator_channels (guild_id, channel_id, PK(guild_id, channel_id))` — bewusst
  **eigene Tabelle statt einer Spalte** in `guild_config`, weil beliebig viele Ersteller-
  Kanäle pro Server möglich sind (n:1-Daten, siehe Punkt 9). `set_tempvoice_creator_channels()`
  ersetzt bei jedem Dashboard-Save die komplette Liste für die Guild (`DELETE` + `executemany`-
  `INSERT`), es gibt kein granulares Hinzufügen/Entfernen einzelner Kanäle auf DB-Ebene.
- Zusätzlich eine eigene Tabelle `warnings (id PK AUTOINCREMENT, guild_id, user_id,
  moderator_id, reason, created_at)` für die `/warn`-Historie (siehe Punkt 7) — bewusst
  keine Spalte in `guild_config`, da es sich um n:1-Daten pro Server handelt (viele
  Verwarnungen pro Guild), nicht um eine einzelne Konfigurationseinstellung.
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
- `LAVALINK_SERVER_PASSWORD` (Musik-System, siehe Punkt 10) — bewusst **kein** Pflichtwert
  (kein `RuntimeError`), da das Musik-Feature optional ist; ist die Variable nicht gesetzt,
  bleibt nur `cogs/music.py` funktionslos, der restliche Bot läuft normal weiter. Name ist
  bewusst identisch zu Lavalinks eigener `application.yml`-Variable gewählt (geteiltes
  Geheimnis zwischen Bot und Lavalink-Node, analog zu `INTERNAL_API_SECRET`).
  `LAVALINK_HOST` (Default `lavalink`, der Service-Name aus `docker-compose.yml`),
  `LAVALINK_PORT` (Default `2333`), `LAVALINK_SECURE` (Default `false`).

Siehe `.env.example` für die vollständige, kommentierte Liste.

## Deployment

**Aktueller Produktivweg (Stand 2026-11-03): GitHub Actions baut, Unraid verwaltet den
Container nativ.** Das war mehrfacher Anlauf wert, hier die Historie und die Fallstricke,
damit nichts versehentlich auf einen früheren Zwischenstand zurückgedreht wird.

### Build & Publish
- `.github/workflows/docker-publish.yml` baut bei jedem Push auf `master` (und per
  `workflow_dispatch`) das Image via `docker/build-push-action` und pusht es als
  `ghcr.io/lon3lyblade/discord-mee6-bot:latest` **und** `:<git-sha>` in die GitHub Container
  Registry. Repository-Owner wird per `${IMAGE_NAME,,}` explizit kleingeschrieben (GHCR
  verlangt lowercase Image-Pfade, `github.repository_owner` liefert aber die Original-
  Schreibweise `Lon3lyBlade`).
- Auth läuft über das automatische `secrets.GITHUB_TOKEN` (kein manuell angelegtes PAT nötig
  für den Workflow selbst, nur Workflow-Permission `packages: write`).
- Das Package ist **privat** (bewusste Nutzerentscheidung) und per "Inherit access from
  source repository" mit dem Repo verknüpft.

### Unraid-Seite: Container als echtes Unraid-Template, nicht per `docker run`
- Der Container wurde manuell über Unraids **"Add Container"**-Formular angelegt (Repository
  `ghcr.io/lon3lyblade/discord-mee6-bot:latest`, Network Type `Bridge`, Port 5000→5001, Pfad
  `/app/data` → `/mnt/user/appdata/discord-bot-dashboard/data`, 6 einzelne `Variable`-Einträge
  für die Secrets aus `.env`) — **nicht** per SSH/`docker run` wie ursprünglich via
  `deploy.sh`. Nur so legt Unraid intern eine Vorlage an, die es für die native
  Update-Erkennung ("up-to-date"/"apply update" in der VERSION-Spalte) und den
  **"Apply Update"**-Button braucht. Ein Container, der nur per rohem `docker run` existiert,
  zeigt dauerhaft "not available" an, selbst wenn das Image öffentlich erreichbar wäre.
- **`deploy.sh` ist damit obsolet für den laufenden Produktiv-Container** — es ist ein
  Überbleibsel aus der Zwischenlösung (zieht das Image per SSH, macht `docker rm -f` +
  `docker run`). Würde es gegen den jetzt laufenden Container ausgeführt, würde es die
  Unraid-Template-Verwaltung **zerstören** (Container wäre danach wieder "not available" und
  der WebUI-Link würde wieder fehlen, siehe unten) und müsste komplett neu über "Add
  Container" aufgesetzt werden. Falls `deploy.sh` nicht mehr gebraucht wird, könnte es entfernt
  oder zumindest mit einer deutlichen Warnung versehen werden.
- **WebUI-Link-Fallstrick:** Der klickbare Link/Icon-Klick in der Unraid-Docker-Übersicht kommt
  aus dem Docker-**Label** `net.unraid.docker.webui` (das alte `deploy.sh` hat das per
  `--label` gesetzt). Beim manuellen Anlegen über "Add Container" gibt es dafür **kein**
  eigenes Textfeld — es muss als zusätzlicher Eintrag vom Typ **"Label"** (nicht "Port"!)
  hinzugefügt werden: Key `net.unraid.docker.webui`, Value `http://[IP]:[PORT:5000]/`
  (Unraid-Platzhalter, `5000` = Container-Port, nicht Host-Port). Ein Port-Eintrag mit dem
  *Namen* "WebUI" (naheliegender erster Versuch) hat **nicht** funktioniert.
- **GHCR-Token-Fallstrick (viel Zeit gekostet):** `docker login ghcr.io` auf Unraid war mit
  einem **classic** Personal Access Token mit Scope `read:packages` erfolgreich (`Login
  Succeeded`), ein anschließender `docker pull` des **privaten** Images schlug trotzdem mit
  `denied` fehl — und zwar reproduzierbar mit frisch generierten Tokens. Isolierter Test:
  Package testweise auf `Public` gestellt → `docker pull` ganz ohne Login funktionierte sofort
  (bestätigt: Netzwerk/Unraid-Docker-Daemon war nie das Problem). Lösung: Token zusätzlich mit
  Scope **`repo`** (Full control of private repositories) ausstatten — offenbar verlangt GHCR
  für Packages, deren Zugriff von einem **privaten** Repo geerbt wird ("Inherit access from
  source repository"), zum Pull-Zeitpunkt einen Nachweis des Repo-Zugriffs, den `read:packages`
  allein nicht liefert. Das steht so nicht in Githubs offizieller Doku (die nennt nur
  `read:packages` als nötig), war hier aber reproduzierbar der entscheidende Unterschied.
  Package danach wieder auf `Private` zurückgestellt.

### Falls nochmal von Null aufgesetzt werden muss
1. Workflow pusht automatisch bei jedem Push — nichts manuell zu tun.
2. Auf dem Zielhost: `docker login ghcr.io -u <user>` mit einem classic PAT, Scopes
   `read:packages` **und** `repo`.
3. Container über Unraids "Add Container"-Formular anlegen (siehe Parameter oben), **nicht**
   über `deploy.sh`.
4. Label `net.unraid.docker.webui` mit `http://[IP]:[PORT:5000]/` ergänzen, falls der
   Dashboard-Link fehlt.
5. Autostart-Toggle für den Container aktivieren.

`docker-compose.yml` bleibt als generisches Referenz-Setup für Nicht-Unraid-Hosts im Repo
(nutzt andere Default-Werte für Name/Port als das Unraid-Setup — falls daran weitergearbeitet
wird, nicht mit dem Unraid-Setup verwechseln).

### Lavalink (Musik-System) auf Unraid

Lavalink ist eine eigenständige Java-Anwendung (siehe Punkt 10 oben) und läuft **nicht**
im Bot-Container mit. Für Unraid bedeutet das: ein **zweiter, separater Unraid-Container**
ist nötig (z. B. das "Lavalink"-Template aus Community Apps, oder manuell über "Add
Container" mit Image `ghcr.io/lavalink-devs/lavalink:4`, Port 2333, `application.yml` als
Pfad-Mapping nach `/opt/Lavalink/application.yml`, `plugins/`-Ordner nach
`/opt/Lavalink/plugins`). Im Bot-Container-Template dann `LAVALINK_HOST`/`LAVALINK_PORT`
auf die IP/den Hostnamen dieses zweiten Containers setzen, `LAVALINK_SERVER_PASSWORD`
identisch zum Passwort in dessen `application.yml`. Für lokale/generische Docker-Hosts
übernimmt `docker-compose.yml` das bereits selbst (eigener `lavalink`-Service im selben
Compose-Netzwerk, erreichbar unter dem Hostnamen `lavalink`).

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
- **Kein Zurücksetzen von Willkommens-Channel/AutoRole/Embed-Channel auf "nichts"** über
  Dashboard oder Slash-Commands möglich (siehe Punkt 6 oben für Details).
- **Keine automatisierten Tests.** Es gibt eine CI-Pipeline (`.github/workflows/docker-publish.yml`),
  die aber nur baut/pusht, nicht testet. Git-Repo liegt auf einem privaten GitHub-Remote
  (`github.com/Lon3lyBlade/discord-mee6-bot`).
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
