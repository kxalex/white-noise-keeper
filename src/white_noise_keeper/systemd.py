from __future__ import annotations

import logging
import os
import socket
import threading


LOG = logging.getLogger(__name__)


class SystemdNotifier:
    def __init__(self) -> None:
        self._socket_path = os.environ.get("NOTIFY_SOCKET")

    @property
    def enabled(self) -> bool:
        return bool(self._socket_path)

    def watchdog_interval_seconds(self) -> float | None:
        if not self.enabled:
            return None
        watchdog_usec = self._watchdog_usec()
        if watchdog_usec is None:
            return None
        # Ping at half the configured watchdog deadline to leave headroom for
        # transient I/O delays.
        return max(1.0, watchdog_usec / 2 / 1_000_000)

    def ready(self) -> None:
        self.notify("READY=1")

    def watchdog(self) -> None:
        self.notify("WATCHDOG=1")

    def status(self, message: str) -> None:
        self.notify(f"STATUS={message}")

    def notify(self, payload: str) -> None:
        if not self._socket_path:
            return
        address = self._socket_path
        if address.startswith("@"):
            address = "\0" + address[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify_socket:
                notify_socket.connect(address)
                notify_socket.sendall(payload.encode("utf-8"))
        except OSError as exc:
            LOG.warning("Systemd notification failed: %s", exc)

    def _watchdog_usec(self) -> int | None:
        raw = os.environ.get("WATCHDOG_USEC")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            LOG.warning("Ignoring invalid WATCHDOG_USEC value: %r", raw)
            return None
        return value if value > 0 else None


def start_watchdog_heartbeat(
    notifier: SystemdNotifier,
) -> threading.Event | None:
    interval = notifier.watchdog_interval_seconds()
    if interval is None:
        return None

    stop_event = threading.Event()

    def run() -> None:
        while not stop_event.is_set():
            try:
                notifier.watchdog()
            except Exception as exc:  # pragma: no cover - best effort logging
                LOG.warning("Systemd watchdog ping failed: %s", exc)
            if stop_event.wait(interval):
                break

    thread = threading.Thread(
        target=run,
        name="white-noise-keeper-watchdog",
        daemon=True,
    )
    thread.start()
    return stop_event
