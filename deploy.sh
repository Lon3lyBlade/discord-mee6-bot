#!/usr/bin/env bash
# Aktualisiert den Bot auf dem Unraid-Server, komplett kostenlos und ohne GitHub Actions/ghcr.io.
# Ausführen auf dem Laptop (im LAN) im Projektordner:  bash deploy.sh
#
# Ablauf:
#   1. Der Projektordner wird per SSH nach Unraid kopiert (ohne .git und .env).
#   2. Das Image wird direkt auf Unraid gebaut und bekommt denselben Namen wie bisher.
#   3. Der laufende Container wird mit SEINER bestehenden Konfiguration (Variablen, Ports,
#      Volumes, Labels, Restart-Policy) neu erstellt. Secrets aus dem Unraid-Template und
#      die Daten unter /app/data (Datenbank, Coins) bleiben dadurch erhalten.
#   4. Startet der neue Container nicht, wird automatisch der alte wiederhergestellt.
#
# Voraussetzung: SSH auf Unraid aktiv und der Container existiert bereits (z.B. über
# "Add Container" angelegt). Eine lokale .env ist NICHT nötig.
#
# Anpassbar per Umgebungsvariable, z.B.:  CONTAINER=meinbot UNRAID_HOST=192.168.x.x bash deploy.sh
set -euo pipefail

UNRAID_HOST="${UNRAID_HOST:-tower.local}"
UNRAID_USER="${UNRAID_USER:-root}"
CONTAINER="${CONTAINER:-discord-bot-dashboard}"
IMAGE="${IMAGE:-ghcr.io/lon3lyblade/discord-mee6-bot:latest}"
BUILD_DIR="${BUILD_DIR:-/mnt/user/appdata/discord-bot-build}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH=(ssh "${UNRAID_USER}@${UNRAID_HOST}")

echo "==> Prüfe Verbindung zu ${UNRAID_HOST} ..."
"${SSH[@]}" "docker info >/dev/null" || { echo "FEHLER: SSH/Docker auf Unraid nicht erreichbar."; exit 1; }
"${SSH[@]}" "docker inspect '${CONTAINER}' >/dev/null 2>&1" || {
  echo "FEHLER: Container '${CONTAINER}' existiert auf Unraid nicht."
  echo "Name prüfen mit:  ssh ${UNRAID_USER}@${UNRAID_HOST} docker ps -a"
  echo "und dann mit  CONTAINER=<name> bash deploy.sh  starten."
  exit 1
}

echo "==> Kopiere Projektdateien nach ${BUILD_DIR} ..."
tar -C "$SCRIPT_DIR" -czf - \
  --exclude=.git --exclude=.env --exclude=data --exclude=__pycache__ --exclude='*.pyc' . \
  | "${SSH[@]}" "rm -rf '${BUILD_DIR}' && mkdir -p '${BUILD_DIR}' && tar -xzf - -C '${BUILD_DIR}'"

echo "==> Baue Image ${IMAGE} auf Unraid (das dauert beim ersten Mal etwas) ..."
"${SSH[@]}" "docker build -t '${IMAGE}' '${BUILD_DIR}'"

echo "==> Erstelle Container ${CONTAINER} mit bestehender Konfiguration neu ..."
"${SSH[@]}" bash -s -- "$CONTAINER" "$IMAGE" <<'REMOTE'
set -euo pipefail
C="$1"; IMG="$2"; OLD="${C}-old"
ENVFILE="$(mktemp)"; trap 'rm -f "$ENVFILE"' EXIT

# Konfiguration des bestehenden Containers auslesen.
# Variablen, die aus dem Image stammen (PATH, Python-Version ...), werden nicht übernommen.
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$C" \
  | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_[A-Z_]*|DB_PATH)=' | grep -v '^$' > "$ENVFILE" || true
mapfile -t BINDS  < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$C" | grep -v '^$' || true)
mapfile -t PORTS  < <(docker inspect -f '{{range $p, $l := .HostConfig.PortBindings}}{{range $l}}{{println .HostPort $p}}{{end}}{{end}}' "$C" | grep -v '^$' || true)
mapfile -t LABELS < <(docker inspect -f '{{range $k, $v := .Config.Labels}}{{$k}}={{$v}}{{"\n"}}{{end}}' "$C" | grep -v '^$' || true)
NETWORK="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$C")"
RESTART="$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$C")"

ARGS=(-d --name "$C" --env-file "$ENVFILE")
[[ -n "$RESTART" && "$RESTART" != "no" ]] && ARGS+=(--restart "$RESTART")
[[ -n "$NETWORK" && "$NETWORK" != "default" ]] && ARGS+=(--network "$NETWORK")
for b in "${BINDS[@]}"; do ARGS+=(-v "$b"); done
for p in "${PORTS[@]}"; do read -r host cont <<<"$p"; ARGS+=(-p "${host}:${cont}"); done
for l in "${LABELS[@]}"; do ARGS+=(--label "$l"); done

# Alten Container nur umbenennen, damit er bei einem Fehler zurückgeholt werden kann.
docker rm -f "$OLD" >/dev/null 2>&1 || true
docker stop "$C" >/dev/null
docker rename "$C" "$OLD"

if docker run "${ARGS[@]}" "$IMG" >/dev/null; then
  sleep 8
  if [[ "$(docker inspect -f '{{.State.Running}}' "$C")" == "true" ]]; then
    docker rm "$OLD" >/dev/null
    echo "Neuer Container läuft."
    exit 0
  fi
fi

echo "FEHLER: Neuer Container startet nicht. Logs:"
docker logs --tail 30 "$C" || true
docker rm -f "$C" >/dev/null 2>&1 || true
docker rename "$OLD" "$C"
docker start "$C" >/dev/null
echo "Alter Container wurde wiederhergestellt."
exit 1
REMOTE

echo "==> Letzte Logs:"
"${SSH[@]}" "docker logs --tail 30 '${CONTAINER}'"
echo
echo "Fertig. Die neuen Slash-Commands erscheinen in Discord nach wenigen Minuten."
