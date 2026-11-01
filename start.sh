#!/usr/bin/env bash
# Startet Discord-Bot und Flask-Dashboard als zwei Prozesse im selben Container,
# damit in Unraid nur ein einziger Container verwaltet werden muss.

cleanup() {
    echo "Beende Bot und Dashboard..."
    kill -TERM "$BOT_PID" "$DASHBOARD_PID" 2>/dev/null
    wait "$BOT_PID" "$DASHBOARD_PID" 2>/dev/null
}
trap cleanup SIGTERM SIGINT

python bot.py &
BOT_PID=$!

python dashboard/app.py &
DASHBOARD_PID=$!

# Stirbt einer der beiden Prozesse, wird der ganze Container beendet,
# damit Docker/Unraid ihn (bei "restart: unless-stopped") sauber neu startet.
wait -n "$BOT_PID" "$DASHBOARD_PID"
cleanup
