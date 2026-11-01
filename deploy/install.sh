#!/usr/bin/env bash
# Install or update research-app as a systemd user service (Linux). Safe to re-run.
#
#   deploy/install.sh                      # port 8765, data in ~/research-data
#   RESEARCH_APP_PORT=9000 deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${RESEARCH_APP_PORT:-8765}"
# Network access is opt-in. The app has no login: anyone who can reach it can run the backends
# as you. RESEARCH_APP_ALLOWED_HOSTS lists the names/IPs browsers will use (space-separated).
HOST="${RESEARCH_APP_HOST:-127.0.0.1}"
ALLOWED_HOSTS="${RESEARCH_APP_ALLOWED_HOSTS:-}"
DATA="${RESEARCH_APP_DATA:-$HOME/research-data}"
CONF_DIR="$HOME/.config/research-app"
UNIT_DIR="$HOME/.config/systemd/user"

python3 -c 'import sys; assert sys.version_info >= (3, 11), "need Python 3.11+, have " + sys.version'

# The service doesn't get your shell's PATH, so record the login PATH: that's where
# the claude and codex CLIs usually live (~/.local/bin, /usr/local/bin, nvm, ...).
LOGIN_PATH="$(bash -lc 'echo "$PATH"')"
for cli in claude codex; do
  PATH="$LOGIN_PATH" command -v "$cli" >/dev/null \
    || echo "warning: $cli not found on your login PATH; that backend will report not_installed"
done

# Claude Code's command sandbox on Linux needs bubblewrap and socat. Without them its
# Bash tool isn't confined to the workspace, so refuse to install rather than run unsafe.
for bin in bwrap socat; do
  command -v "$bin" >/dev/null || {
    echo "error: $bin is required for the Claude Code sandbox: sudo apt install bubblewrap socat" >&2
    exit 1
  }
done

mkdir -p "$CONF_DIR" "$UNIT_DIR" "$DATA"
cat > "$CONF_DIR/env" <<ENV
PATH=$LOGIN_PATH
RESEARCH_APP_PORT=$PORT
RESEARCH_APP_DATA=$DATA
RESEARCH_APP_HOST=$HOST
RESEARCH_APP_ALLOWED_HOSTS=$ALLOWED_HOSTS
ENV
sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/deploy/research-app.service" > "$UNIT_DIR/research-app.service"

systemctl --user daemon-reload
systemctl --user enable research-app.service >/dev/null
systemctl --user restart research-app.service
# Keep the service running after you log out and start it at boot.
loginctl enable-linger "$USER" 2>/dev/null || true

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/api/topics" >/dev/null 2>&1; then
    echo "research-app is running (data: $DATA)"
    for name in 127.0.0.1 $ALLOWED_HOSTS; do echo "  http://$name:$PORT"; done
    [ "$HOST" = 127.0.0.1 ] && echo "Local only. From another machine: ssh -N -L $PORT:127.0.0.1:$PORT <this-host>"
    exit 0
  fi
  sleep 0.5
done
echo "error: service did not come up; see: journalctl --user -u research-app -n 50" >&2
exit 1
