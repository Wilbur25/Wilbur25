#!/usr/bin/env bash
# Install goeswx on this machine and schedule it with systemd user timers.
# Usage: ./deploy/install.sh   (run from anywhere; no root needed except for the apt line)
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
    echo "Need python3-venv:  sudo apt install python3-venv python3-pip ffmpeg"
    exit 1
fi

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    echo ">>> Created config.yaml - edit your location, station and map region, then re-run this script."
    exit 0
fi

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
for f in deploy/systemd/*; do
    sed "s#@APP_DIR@#$APP_DIR#g" "$f" > "$UNIT_DIR/$(basename "$f")"
done
systemctl --user daemon-reload
systemctl --user enable --now goeswx-tick.timer goeswx-forecast.timer goeswx-train.timer goeswx-serve.service

# Keep the timers running when you're not logged in.
loginctl enable-linger "$USER" 2>/dev/null || echo "(run 'sudo loginctl enable-linger $USER' so it runs while logged out)"

echo "First run: building the forecast and the last 2 hours of imagery..."
.venv/bin/goeswx forecast || true
.venv/bin/goeswx imagery --hours 2 || true
.venv/bin/goeswx tick || true

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Done. Dashboard: http://localhost:8080/  (on your network: http://${IP:-<this-machine>}:8080/)"
echo "Logs:  journalctl --user -u goeswx-tick -u goeswx-forecast -f"
