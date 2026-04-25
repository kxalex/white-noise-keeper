from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlparse

from .stats import render_stats_table

LOG = logging.getLogger(__name__)

ACTION_METHODS: dict[str, str] = {
    "/v1/actions/start": "command_start",
    "/v1/actions/stop": "command_stop",
}


def start_api_server(keeper, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(keeper))
    thread = threading.Thread(
        target=server.serve_forever,
        name="white-noise-keeper-api",
        daemon=True,
    )
    thread.start()
    LOG.info("HTTP API listening on %s:%s", host, port)
    return server


def make_handler(keeper) -> type[BaseHTTPRequestHandler]:
    class WhiteNoiseKeeperHandler(BaseHTTPRequestHandler):
        server_version = "WhiteNoiseKeeperHTTP/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/v1/status":
                self._run_command(keeper.status_snapshot)
                return
            if parsed.path == "/v1/stats":
                if _wants_json(self.headers.get("Accept")):
                    self._run_command(keeper.stats_snapshot)
                    return
                self._run_stats_table()
                return
            if parsed.path in ACTION_METHODS:
                self._write_json(405, {"ok": False, "error": "method not allowed"})
                return
            self._write_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            method_name = ACTION_METHODS.get(parsed.path)
            if method_name is None:
                self._write_json(404, {"ok": False, "error": "not found"})
                return
            self._skip_access_log = True
            self._log_access(200)
            try:
                self._run_command(getattr(keeper, method_name))
            finally:
                self._skip_access_log = False

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def log_message(self, fmt: str, *args) -> None:
            LOG.info("%s - %s", self.address_string(), fmt % args)

        def log_request(self, code: str | int = "-", size: str | int = "-") -> None:
            if getattr(self, "_skip_access_log", False):
                return
            super().log_request(code, size)

        def _method_not_allowed(self) -> None:
            self._write_json(405, {"ok": False, "error": "method not allowed"})

        def _run_command(self, command: Callable[[], dict]) -> None:
            try:
                self._write_json(200, command())
            except Exception as exc:
                LOG.exception("HTTP API command failed")
                self._write_json(500, {"ok": False, "error": str(exc)})

        def _run_stats_table(self) -> None:
            try:
                clock = getattr(keeper, "clock", time.time)
                now_seconds = clock()
                payload = keeper.stats_snapshot()
                body = render_stats_table(payload, now_seconds).encode("utf-8")
                self._write_response(200, body, "text/plain; charset=utf-8")
            except Exception as exc:
                LOG.exception("HTTP API stats table failed")
                self._write_json(500, {"ok": False, "error": str(exc)})

        def _write_json(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self._write_response(status_code, body, "application/json")

        def _write_response(
            self,
            status_code: int,
            body: bytes,
            content_type: str,
        ) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _log_access(self, code: str | int, size: str | int = "-") -> None:
            LOG.info(
                '%s - "%s %s %s" %s %s',
                self.address_string(),
                self.command,
                self.path,
                self.request_version,
                code,
                size,
            )

    return WhiteNoiseKeeperHandler


def _wants_json(accept_header: str | None) -> bool:
    if not accept_header:
        return False
    return "application/json" in accept_header.lower()
