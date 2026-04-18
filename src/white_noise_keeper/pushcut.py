from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)


@dataclass
class PushcutClient:
    play_url: str
    stop_url: str
    timeout_seconds: float = 10.0

    def trigger_play(self, dry_run: bool = False) -> None:
        self._post(self.play_url, "play", dry_run)

    def trigger_stop(self, dry_run: bool = False) -> None:
        self._post(self.stop_url, "stop", dry_run)

    def _post(self, url: str, action: str, dry_run: bool) -> None:
        if not url:
            raise ValueError(f"Pushcut {action} URL is empty")
        if dry_run:
            LOG.info("Dry run: would trigger Pushcut %s URL", action)
            return
        request = Request(url, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                LOG.info(
                    "Pushcut %s request accepted with HTTP %s",
                    action,
                    response.status,
                )
        except HTTPError as exc:
            raise RuntimeError(f"Pushcut {action} request failed: HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Pushcut {action} request failed: {exc.reason}") from exc
