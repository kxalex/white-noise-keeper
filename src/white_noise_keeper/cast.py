from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .config import CastConfig

LOG = logging.getLogger(__name__)


PLAYER_PLAYING = "PLAYING"
PLAYER_BUFFERING = "BUFFERING"
PLAYER_PAUSED = "PAUSED"
PLAYER_IDLE = "IDLE"


@dataclass(frozen=True)
class CastState:
    content_id: str | None
    player_state: str | None
    current_time: float | None
    duration: float | None
    volume_muted: bool | None
    volume_level: float | None

    @property
    def playing(self) -> bool:
        return self.player_state in {PLAYER_PLAYING, PLAYER_BUFFERING}

    @property
    def paused(self) -> bool:
        return self.player_state == PLAYER_PAUSED


class CastClient(Protocol):
    def get_state(self) -> CastState:
        ...

    def load(self, autoplay: bool) -> None:
        ...

    def play(self) -> None:
        ...

    def pause(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def set_muted(self, muted: bool) -> None:
        ...

    def close(self) -> None:
        ...


class PyChromecastClient:
    def __init__(self, config: CastConfig):
        self.config = config
        self._cast = None
        self._browser = None

    def connect(self) -> None:
        import pychromecast

        kwargs = {
            "friendly_names": [self.config.name],
            "discovery_timeout": self.config.discovery_timeout_seconds,
        }
        if self.config.known_hosts:
            kwargs["known_hosts"] = list(self.config.known_hosts)

        chromecasts, browser = pychromecast.get_listed_chromecasts(**kwargs)
        if not chromecasts:
            raise RuntimeError(f"Chromecast {self.config.name!r} was not discovered")
        self._browser = browser
        self._cast = chromecasts[0]
        self._cast.wait(timeout=self.config.discovery_timeout_seconds)
        LOG.info("Connected to %s", self.config.name)

    def get_state(self) -> CastState:
        cast = self._require_cast()
        media = cast.media_controller
        _refresh_media_status(media)
        status = media.status
        cast_status = cast.status
        return CastState(
            content_id=getattr(status, "content_id", None),
            player_state=getattr(status, "player_state", None),
            current_time=_optional_float(getattr(status, "current_time", None)),
            duration=_optional_float(getattr(status, "duration", None)),
            volume_muted=getattr(cast_status, "volume_muted", None),
            volume_level=_optional_float(getattr(cast_status, "volume_level", None)),
        )

    def load(self, autoplay: bool) -> None:
        cast = self._require_cast()
        media = cast.media_controller
        media.play_media(
            self.config.url,
            self.config.content_type,
            autoplay=autoplay,
            stream_type="BUFFERED",
        )
        media.block_until_active(timeout=10)

    def play(self) -> None:
        self._require_cast().media_controller.play()

    def pause(self) -> None:
        self._require_cast().media_controller.pause()

    def stop(self) -> None:
        self._require_cast().media_controller.stop()

    def set_muted(self, muted: bool) -> None:
        self._require_cast().set_volume_muted(muted)

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except AttributeError:
                import pychromecast

                pychromecast.discovery.stop_discovery(self._browser)
        self._browser = None
        self._cast = None

    def _require_cast(self):
        if self._cast is None:
            self.connect()
        return self._cast


def expected_media_loaded(state: CastState, expected_url: str) -> bool:
    return state.content_id == expected_url


def _optional_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _refresh_media_status(media) -> None:
    refreshed = threading.Event()

    def callback(_sent, _response):
        refreshed.set()

    try:
        media.update_status(callback_function=callback)
    except TypeError:
        media.update_status()
        time.sleep(0.2)
        return

    refreshed.wait(timeout=2)
