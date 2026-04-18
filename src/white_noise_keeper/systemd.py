from __future__ import annotations

import os
import socket


class SystemdNotifier:
    def __init__(self) -> None:
        self._socket_path = os.environ.get("NOTIFY_SOCKET")

    @property
    def enabled(self) -> bool:
        return bool(self._socket_path)

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
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify_socket:
            notify_socket.connect(address)
            notify_socket.sendall(payload.encode("utf-8"))
