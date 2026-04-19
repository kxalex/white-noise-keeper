# White Noise Keeper

Long-running Raspberry Pi service that keeps a Google Nest playing white noise during sleep hours. iPad backup support is implemented but disabled by default; enable it later after Pushcut Automation Server is installed and tested.

## Install On Raspberry Pi

Run this on the Raspberry Pi:

```sh
START_SERVICE=0 sh -c "$(curl -fsSL https://raw.githubusercontent.com/kxalex/white-noise-keeper/master/scripts/install-or-update-on-pi.sh)"
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

Leave `ipad_backup.enabled = false` until Pushcut is ready.

## Updating On Raspberry Pi

After the first install, run:

```sh
update-white-noise-keeper
```

That command pulls the latest Git changes, reinstalls the package, runs the unit tests, restarts the systemd service, and prints service status.

For a nonstandard checkout path:

```sh
REPO_DIR=/path/to/white-noise-keeper update-white-noise-keeper
```

To skip tests during an urgent update:

```sh
RUN_TESTS=0 update-white-noise-keeper
```

## Commands

Inspect and repair once:

```sh
white-noise-keeper --config /etc/white-noise-keeper/config.toml --once --debug
```

Validate Pushcut play URL without sending it, after Pushcut URLs are configured:

```sh
white-noise-keeper --config /etc/white-noise-keeper/config.toml --trigger-ipad-backup --dry-run
```

Send Pushcut play request:

```sh
white-noise-keeper --config /etc/white-noise-keeper/config.toml --trigger-ipad-backup
```

Send Pushcut stop request:

```sh
white-noise-keeper --config /etc/white-noise-keeper/config.toml --stop-ipad-backup
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

Start and keep white noise running until `stop` or the configured end time:

```sh
curl -X POST http://<pi-ip>:8765/v1/actions/start-force
```

Stop white noise and suppress automatic restart until the next active window:

```sh
curl -X POST http://<pi-ip>:8765/v1/actions/stop
```

## Pushcut Setup

This section is optional. The live service does not use Pushcut while `ipad_backup.enabled = false`.

Create two iPad Shortcuts:

- `Play White Noise Backup`: set iPad volume, play the selected Music track or playlist, and enable repeat if needed.
- `Stop White Noise Backup`: stop Music playback.

Run Pushcut Automation Server on the iPad, keep Pushcut in the foreground, and place the two execute URLs in the config. Use `timeout=nowait` in both URLs so the Pi does not block while the iPad runs a shortcut.

Then set:

```toml
[ipad_backup]
enabled = true
play_url = "https://api.pushcut.io/<replace-me>/execute?shortcut=Play%20White%20Noise%20Backup&timeout=nowait"
stop_url = "https://api.pushcut.io/<replace-me>/execute?shortcut=Stop%20White%20Noise%20Backup&timeout=nowait"
```

## Notes

The service checks every 2 seconds while inside the active window. systemd does not restart it every 2 seconds; systemd starts it at boot and restarts it only after failure.

PyChromecast uses mDNS for discovery. If discovery is unreliable, set `known_hosts` in the config.

## Packaging Options

- Git checkout plus the install/update script: best current option. It is simple, transparent, easy to debug on the Pi, and keeps `/etc/white-noise-keeper/config.toml` outside Git.
- Python wheel: useful when the code stabilizes. Build with `python -m build`, copy the wheel to the Pi, and install it into `/opt/white-noise-keeper`.
- Debian package: best if this will run on multiple Pis or you want apt-managed upgrades. More setup, but it can own the system user, config path, venv/package install, and systemd unit.
- Container: possible, but not recommended as the first option because Chromecast/mDNS discovery and host networking make it more fragile than a direct systemd service.
