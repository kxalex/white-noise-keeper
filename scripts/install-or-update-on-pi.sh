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
UV_INSTALL_URL="${UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="${SUDO:-sudo}"; fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required but is not installed." >&2
  if command -v apt-get >/dev/null 2>&1; then
    echo "Install it with: sudo apt-get update && sudo apt-get install -y git" >&2
  else
    echo "Install git with your system package manager and rerun this script." >&2
  fi
  exit 1
fi

UV_BIN="${UV_BIN:-}"
if [ -z "$UV_BIN" ]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  elif [ -x "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
  else
    curl -LsSf "$UV_INSTALL_URL" | sh
    if command -v uv >/dev/null 2>&1; then
      UV_BIN="$(command -v uv)"
    elif [ -x "$HOME/.local/bin/uv" ]; then
      UV_BIN="$HOME/.local/bin/uv"
    else
      echo "uv installer finished, but uv was not found. Set UV_BIN=/path/to/uv and rerun." >&2
      exit 1
    fi
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3.11 >/dev/null 2>&1; then PYTHON_BIN=python3.11; else PYTHON_BIN=python3; fi
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'

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

$SUDO "$UV_BIN" venv --python "$PYTHON_BIN" "$VENV_DIR"
$SUDO "$UV_BIN" pip install --python "$VENV_DIR/bin/python" "$REPO_DIR"

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
  echo "Restarting systemd service: $SERVICE"
  $SUDO systemctl restart "$SERVICE"
  echo "Service restarted. Current status:"
  $SUDO systemctl --no-pager --full status "$SERVICE" || true
else
  echo "Skipping service restart because START_SERVICE=0"
fi

echo "Done. Config: $CONFIG_FILE"
echo "Update later with: $UPDATE_BIN"
echo "Logs: journalctl -u $SERVICE -f"
