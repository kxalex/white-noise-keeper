from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

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
            if self.path != "/v1/status":
                if self.path in ACTION_METHODS:
                    self._write_json(405, {"ok": False, "error": "method not allowed"})
                    return
                self._write_json(404, {"ok": False, "error": "not found"})
                return
            self._run_command(keeper.status_snapshot)

        def do_POST(self) -> None:
            method_name = ACTION_METHODS.get(self.path)
            if method_name is None:
                self._write_json(404, {"ok": False, "error": "not found"})
                return
            self._run_command(getattr(keeper, method_name))

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def log_message(self, fmt: str, *args) -> None:
            LOG.info("%s - %s", self.address_string(), fmt % args)

        def _method_not_allowed(self) -> None:
            self._write_json(405, {"ok": False, "error": "method not allowed"})

        def _run_command(self, command: Callable[[], dict]) -> None:
            try:
                self._write_json(200, command())
            except Exception as exc:
                LOG.exception("HTTP API command failed")
                self._write_json(500, {"ok": False, "error": str(exc)})

        def _write_json(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return WhiteNoiseKeeperHandler
