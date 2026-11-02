#!/usr/bin/env bash
# Aktualisiert den Discord-Bot (Bot + Flask-Dashboard) auf dem Unraid-Server "Tower".
# Ausführen auf dem Laptop (im LAN) im Projektordner:  bash deploy.sh
#
# Das Image wird nicht mehr auf Unraid gebaut. Stattdessen baut GitHub Actions bei
# jedem Push auf master automatisch ein Docker-Image und lädt es in die GitHub
# Container Registry (ghcr.io) hoch (siehe .github/workflows/docker-publish.yml).
# Unraids Docker-Tab erkennt darüber von sich aus, wenn eine neue Version bereitsteht
# ("Update Ready"-Button) - dieses Skript holt das neueste Image zusätzlich sofort,
# falls du nicht auf Unraids automatischen Check warten willst.
#
# Einmalige Einrichtung (das GitHub-Package ist privat, Unraid braucht also einen
# eigenen Zugang zur Registry):
#   1. Auf https://github.com/settings/tokens ein "Personal access token (classic)"
#      mit Scope "read:packages" erstellen.
#   2. Auf Unraid per SSH einmalig einloggen:
#        docker login ghcr.io -u Lon3lyBlade
#      (Token als Passwort einfügen, wenn danach gefragt wird - NICHT in die Kommandozeile
#      schreiben, damit es nicht in der Shell-History/Prozessliste landet.)
# Voraussetzung: SSH auf Unraid aktiv, lokale .env ausgefüllt (Kopie von .env.example).
set -euo pipefail

UNRAID_HOST="${UNRAID_HOST:-192.168.178.41}"
UNRAID_USER="${UNRAID_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/mnt/user/appdata/discord-bot-dashboard}"
CONTAINER="${CONTAINER:-discord-bot-dashboard}"
IMAGE="${IMAGE:-ghcr.io/lon3lyblade/discord-mee6-bot:latest}"
HOST_PORT="${HOST_PORT:-5001}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH="ssh ${UNRAID_USER}@${UNRAID_HOST}"

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "FEHLER: $SCRIPT_DIR/.env fehlt."
  echo "Kopiere .env.example nach .env und trage Token, Client-ID/-Secret und zufällige Secrets ein."
  echo "DISCORD_REDIRECT_URI sollte http://${UNRAID_HOST}:${HOST_PORT}/callback sein."
  exit 1
fi
if grep -qE 'dein-bot-token-hier|dein-client-secret|bitte-durch-einen-zufaelligen-wert-ersetzen|deine-client-id' "$SCRIPT_DIR/.env"; then
  echo "FEHLER: .env enthält noch Platzhalter aus .env.example. Bitte alle Werte ausfüllen."
  exit 1
fi

echo "==> Prüfe Verbindung und Port ${HOST_PORT} auf ${UNRAID_HOST} ..."
$SSH "docker info >/dev/null" || { echo "FEHLER: SSH/Docker auf Unraid nicht erreichbar."; exit 1; }
BLOCKER="$($SSH "docker ps --format '{{.Names}} {{.Ports}}'" | grep -E "[:>]${HOST_PORT}->" | grep -v "^${CONTAINER} " || true)"
if [[ -n "$BLOCKER" ]]; then
  echo "FEHLER: Port ${HOST_PORT} wird bereits von einem anderen Container belegt:"
  echo "  $BLOCKER"
  echo "Stoppe ihn oder starte mit einem anderen Port, z.B.:  HOST_PORT=5001 bash deploy.sh"
  exit 1
fi

echo "==> Kopiere lokale .env nach Unraid ..."
$SSH "mkdir -p '${REMOTE_DIR}/data'"
scp "$SCRIPT_DIR/.env" "${UNRAID_USER}@${UNRAID_HOST}:${REMOTE_DIR}/.env"
$SSH "chmod 600 '${REMOTE_DIR}/.env'"

echo "==> Hole neuestes Image von der GitHub Container Registry ..."
$SSH "docker pull '${IMAGE}'" || {
  echo "FEHLER: 'docker pull' fehlgeschlagen. Falls das Package privat ist, einmalig auf"
  echo "Unraid einloggen:  docker login ghcr.io -u Lon3lyBlade"
  exit 1
}

echo "==> Starte Container ${CONTAINER} neu ..."
$SSH "docker rm -f '${CONTAINER}' >/dev/null 2>&1 || true; \
  docker run -d --name '${CONTAINER}' --restart unless-stopped \
    --env-file '${REMOTE_DIR}/.env' \
    -p '${HOST_PORT}:5000' \
    --label "net.unraid.docker.webui=http://${UNRAID_HOST}:${HOST_PORT}" \
    -v '${REMOTE_DIR}/data:/app/data' \
    '${IMAGE}'"

sleep 6
echo "==> Letzte Logs:"
$SSH "docker logs --tail 30 '${CONTAINER}'"

echo
echo "Fertig. Dashboard: http://${UNRAID_HOST}:${HOST_PORT}"
echo "Vergiss nicht, http://${UNRAID_HOST}:${HOST_PORT}/callback im Discord Developer Portal (OAuth2 > Redirects) einzutragen."
