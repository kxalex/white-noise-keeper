# White Noise Keeper

Long-running Raspberry Pi service that keeps a Google Nest white-noise session alive and restores the last successful cast state after disconnects.

## Install On Raspberry Pi

Run this on the Raspberry Pi:

```sh
START_SERVICE=0 sh -c "$(curl -fsSL https://raw.githubusercontent.com/kxalex/white-noise-keeper/master/scripts/install-or-update-on-pi.sh)"
```

Prerequisites: `curl`, `git`, and Python 3.11 or newer. On Raspberry Pi OS, install missing prerequisites with:

```sh
sudo apt-get update && sudo apt-get install -y curl git python3
```

The installer:

- Creates `/opt/white-noise-keeper`.
- Pulls the latest Git version on every run.
- Installs `uv` if needed.
- Installs the Python package and dependencies into the venv with `uv`.
- Creates a locked-down `white-noise-keeper` system user.
- Copies `config.example.toml` to `/etc/white-noise-keeper/config.toml` only if that file does not already exist.
- Installs and enables the systemd service.
- Installs `/usr/local/bin/update-white-noise-keeper`.

Edit `/etc/white-noise-keeper/config.toml` and set the real Nest name, stream URL, and known host if needed. Then start the service:

```sh
sudo systemctl start white-noise-keeper
```

## Updating On Raspberry Pi

After the first install, run:

```sh
update-white-noise-keeper
```

That command pulls the latest Git changes, reuses the existing virtual environment, reinstalls the package, runs the unit tests, restarts the systemd service, and prints service status.

To replace the virtual environment before installing:

```sh
update-white-noise-keeper --fresh
```

For a nonstandard checkout path:

```sh
REPO_DIR=/path/to/white-noise-keeper update-white-noise-keeper
```

To skip tests during an urgent update:

```sh
RUN_TESTS=0 update-white-noise-keeper
```

To update without restarting the service:

```sh
START_SERVICE=0 update-white-noise-keeper
```

## Commands

Inspect and repair once:

```sh
white-noise-keeper --config /etc/white-noise-keeper/config.toml --once --debug
```

Watch service logs:

```sh
journalctl -u white-noise-keeper -f
```

## HTTP API

The keeper exposes an unauthenticated LAN API by default:

```toml
[http]
enabled = true
host = "0.0.0.0"
port = 8765
```

Only run this on a trusted LAN or put nginx/firewall rules in front of it. The API uses a high port so nginx can keep `80` and `443`.

Status:

```sh
curl http://<pi-ip>:8765/v1/status
```

Start white noise once:

```sh
curl -X POST http://<pi-ip>:8765/v1/actions/start
```

Stop white noise by pausing and rewinding it. You can start it again from the Nest or API:

```sh
curl -X POST http://<pi-ip>:8765/v1/actions/stop
```

## Notes

The service checks every 2 seconds. systemd watchdog pings run in a separate heartbeat thread, so a slow Chromecast reconnect no longer starves the watchdog and kills the process.

PyChromecast uses mDNS for discovery. If discovery is unreliable, set `known_hosts` in the config. The default discovery timeout is now shorter, so `known_hosts` matters more on flaky networks.

## Packaging Options

- Git checkout plus the install/update script: best current option. It is simple, transparent, easy to debug on the Pi, and keeps `/etc/white-noise-keeper/config.toml` outside Git.
- Python wheel: useful when the code stabilizes. Build with `python -m build`, copy the wheel to the Pi, and install it into `/opt/white-noise-keeper`.
- Debian package: best if this will run on multiple Pis or you want apt-managed upgrades. More setup, but it can own the system user, config path, venv/package install, and systemd unit.
- Container: possible, but not recommended as the first option because Chromecast/mDNS discovery and host networking make it more fragile than a direct systemd service.
