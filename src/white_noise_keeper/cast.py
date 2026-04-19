from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .config import CastConfig

LOG = logging.getLogger(__name__)
VOLUME_CONFIRM_TIMEOUT_SECONDS = 2.0
VOLUME_CONFIRM_INTERVAL_SECONDS = 0.05
VOLUME_LEVEL_TOLERANCE = 0.01


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

    def set_volume_level(self, level: float) -> None:
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
        status = media.status if _refresh_media_status(media) else None
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

    def set_volume_level(self, level: float) -> None:
        LOG.info("Setting Chromecast volume level to %.2f", level)
        cast = self._require_cast()
        requested_level = cast.set_volume(level)
        _wait_for_volume_level(cast, requested_level)
        LOG.info("Chromecast volume confirmed at %.2f", requested_level)

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


def _refresh_media_status(media) -> bool:
    if not _can_refresh_media_status_without_launch(media):
        LOG.debug("Skipping media status refresh because media app is not running")
        return False

    refreshed = threading.Event()

    def callback(_sent, _response):
        refreshed.set()

    try:
        media.update_status(callback_function=callback)
    except TypeError:
        media.update_status()
        time.sleep(0.2)
        return True

    refreshed.wait(timeout=2)
    return True


def _wait_for_volume_level(cast, expected_level: float) -> None:
    deadline = time.monotonic() + VOLUME_CONFIRM_TIMEOUT_SECONDS
    last_level = _optional_float(getattr(cast.status, "volume_level", None))

    while time.monotonic() < deadline:
        if _volume_level_matches(last_level, expected_level):
            return

        _refresh_receiver_status(cast)
        last_level = _optional_float(getattr(cast.status, "volume_level", None))
        if _volume_level_matches(last_level, expected_level):
            return

        time.sleep(VOLUME_CONFIRM_INTERVAL_SECONDS)

    raise TimeoutError(
        "Chromecast volume did not reach "
        f"{expected_level:.2f}; last reported volume was {last_level}"
    )


def _volume_level_matches(actual: float | None, expected: float) -> bool:
    return actual is not None and abs(actual - expected) <= VOLUME_LEVEL_TOLERANCE


def _refresh_receiver_status(cast) -> None:
    receiver = getattr(getattr(cast, "socket_client", None), "receiver_controller", None)
    if receiver is None:
        return

    refreshed = threading.Event()

    def callback(_sent, _response):
        refreshed.set()

    receiver.update_status(callback_function=callback)
    refreshed.wait(timeout=0.5)


def _can_refresh_media_status_without_launch(media) -> bool:
    socket_client = getattr(media, "_socket_client", None)
    if socket_client is None:
        return False
    if getattr(media, "target_platform", False):
        return True

    namespace = getattr(media, "namespace", None)
    app_namespaces = getattr(socket_client, "app_namespaces", ())
    if namespace not in app_namespaces:
        return False

    if getattr(media, "app_must_match", False):
        receiver_controller = getattr(socket_client, "receiver_controller", None)
        app_id = getattr(receiver_controller, "app_id", None)
        if app_id != getattr(media, "supporting_app_id", None):
            return False

    return True
