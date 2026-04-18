#!/usr/bin/env sh
set -eu

SERVICE="${SERVICE:-white-noise-keeper}"
APP_USER="${APP_USER:-white-noise-keeper}"
REPO_URL="${REPO_URL:-https://github.com/kxalex/white-noise-keeper.git}"
REPO_DIR="${REPO_DIR:-/opt/src/white-noise-keeper}"
VENV_DIR="${VENV_DIR:-/opt/white-noise-keeper}"
CONFIG_DIR="${CONFIG_DIR:-/etc/white-noise-keeper}"
CONFIG_FILE="${CONFIG_FILE:-$CONFIG_DIR/config.toml}"
UPDATE_BIN="${UPDATE_BIN:-/usr/local/bin/update-white-noise-keeper}"
RUN_TESTS="${RUN_TESTS:-1}"
START_SERVICE="${START_SERVICE:-1}"

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="${SUDO:-sudo}"; fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3.11 >/dev/null 2>&1; then PYTHON_BIN=python3.11; else PYTHON_BIN=python3; fi
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'

if command -v apt-get >/dev/null 2>&1; then
  $SUDO apt-get update
  $SUDO apt-get install -y git python3-venv python3-pip
fi

if [ ! -d "$REPO_DIR/.git" ]; then
  $SUDO mkdir -p "$(dirname "$REPO_DIR")"
  $SUDO chown "$(id -u):$(id -g)" "$(dirname "$REPO_DIR")"
  git clone "$REPO_URL" "$REPO_DIR"
else
  git -C "$REPO_DIR" pull --ff-only
fi

NOLOGIN=/usr/sbin/nologin
[ -x "$NOLOGIN" ] || NOLOGIN=/bin/false
id "$APP_USER" >/dev/null 2>&1 || \
  $SUDO useradd --system --user-group --home-dir "/var/lib/$SERVICE" --shell "$NOLOGIN" "$APP_USER"

$SUDO "$PYTHON_BIN" -m venv "$VENV_DIR"
$SUDO "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel
$SUDO "$VENV_DIR/bin/pip" install --no-build-isolation "$REPO_DIR"

$SUDO mkdir -p "$CONFIG_DIR"
[ -f "$CONFIG_FILE" ] || $SUDO install -m 0644 "$REPO_DIR/config.example.toml" "$CONFIG_FILE"
$SUDO install -m 0644 "$REPO_DIR/systemd/white-noise-keeper.service" "/etc/systemd/system/$SERVICE.service"
$SUDO install -m 0755 "$REPO_DIR/scripts/install-or-update-on-pi.sh" "$UPDATE_BIN"

if [ "$RUN_TESTS" = "1" ]; then
  "$VENV_DIR/bin/python" -m unittest discover -s "$REPO_DIR/tests" -v
fi

$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE"
if [ "$START_SERVICE" = "1" ]; then
  $SUDO systemctl restart "$SERVICE"
  $SUDO systemctl --no-pager --full status "$SERVICE" || true
fi

echo "Done. Config: $CONFIG_FILE"
echo "Update later with: $UPDATE_BIN"
echo "Logs: journalctl -u $SERVICE -f"
