#!/usr/bin/env bash
# Deployt den Discord-Bot (Bot + Flask-Dashboard) auf den Unraid-Server "Tower".
# Ausführen auf dem Laptop (im LAN) im Projektordner:  bash deploy.sh
# Voraussetzung: SSH auf Unraid aktiv, lokale .env ausgefüllt (Kopie von .env.example).
set -euo pipefail

UNRAID_HOST="${UNRAID_HOST:-192.168.178.41}"
UNRAID_USER="${UNRAID_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/mnt/user/appdata/discord-bot-dashboard}"
CONTAINER="${CONTAINER:-discord-bot-dashboard}"
IMAGE="${IMAGE:-discord-bot-dashboard}"
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

echo "==> Lade Projekt nach ${REMOTE_DIR} hoch (data/ bleibt unangetastet) ..."
$SSH "mkdir -p '${REMOTE_DIR}/data'"
tar czf - -C "$SCRIPT_DIR" \
  --exclude='./.git' --exclude='./data' --exclude='./.venv' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='./deploy.sh' . \
  | $SSH "tar xzf - -C '${REMOTE_DIR}' && chmod 600 '${REMOTE_DIR}/.env'"

echo "==> Baue Docker-Image ..."
$SSH "cd '${REMOTE_DIR}' && docker build -t '${IMAGE}' ."

echo "==> Starte Container ${CONTAINER} ..."
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
