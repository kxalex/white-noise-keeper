from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .config import CastConfig

LOG = logging.getLogger(__name__)
VOLUME_CONFIRM_TIMEOUT_SECONDS = 1.0
VOLUME_CONFIRM_INTERVAL_SECONDS = 0.05
MEDIA_LOAD_CONFIRM_TIMEOUT_SECONDS = 5.0
MEDIA_LOAD_CONFIRM_INTERVAL_SECONDS = 0.1
MEDIA_STATUS_REFRESH_TIMEOUT_SECONDS = 2.0
CAST_DISCONNECT_TIMEOUT_SECONDS = 1.0


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


class CastClient(Protocol):
    def get_state(self) -> CastState:
        ...

    def load(self, autoplay: bool) -> None:
        ...

    def play(self) -> None:
        ...

    def pause(self) -> None:
        ...

    def seek_to_start(self) -> None:
        ...

    def set_muted(self, muted: bool) -> None:
        ...

    def reset(self) -> None:
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
        loaded = threading.Event()
        load_result = {}

        def callback(sent, response):
            load_result["sent"] = sent
            load_result["response"] = response
            loaded.set()

        LOG.info("Loading media: %s", self.config.url)
        media.play_media(
            self.config.url,
            self.config.content_type,
            autoplay=autoplay,
            stream_type="BUFFERED",
            callback_function=callback,
        )
        if not loaded.wait(timeout=MEDIA_LOAD_CONFIRM_TIMEOUT_SECONDS):
            raise TimeoutError("Chromecast media load command did not complete")
        if load_result.get("sent") is False:
            raise RuntimeError(
                f"Chromecast media load command failed: {load_result.get('response')}"
            )
        media.block_until_active(timeout=5)
        _wait_for_media_loaded(media, self.config.url)
        LOG.info("Media confirmed loaded: %s", self.config.url)

    def play(self) -> None:
        self._require_cast().media_controller.play()

    def pause(self) -> None:
        LOG.debug("Pausing media")
        self._require_cast().media_controller.pause()
        LOG.info("Media paused")

    def seek_to_start(self) -> None:
        LOG.debug("Seeking media to start")
        self._require_cast().media_controller.seek(0)
        LOG.info("Media seeked to start")

    def set_muted(self, muted: bool) -> None:
        cast = self._require_cast()
        cast.set_volume_muted(muted)
        _wait_for_volume_muted(cast, muted)
        LOG.info("Muted state is %s", muted)

    def close(self) -> None:
        cast = self._cast
        browser = self._browser
        self._cast = None
        self._browser = None

        if cast is not None:
            disconnect = getattr(cast, "disconnect", None)
            if callable(disconnect):
                try:
                    disconnect(timeout=CAST_DISCONNECT_TIMEOUT_SECONDS)
                except Exception as exc:  # pragma: no cover - best effort teardown
                    LOG.debug("Chromecast disconnect failed during close: %s", exc)

        if browser is not None:
            try:
                browser.stop_discovery()
            except AttributeError:
                import pychromecast

                pychromecast.discovery.stop_discovery(browser)
            except Exception as exc:  # pragma: no cover - best effort teardown
                LOG.debug("Chromecast discovery stop failed during close: %s", exc)

    def reset(self) -> None:
        LOG.info("Resetting Chromecast connection")
        self.close()

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


def _refresh_media_status(
    media,
    timeout: float = MEDIA_STATUS_REFRESH_TIMEOUT_SECONDS,
) -> bool:
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

    if not refreshed.wait(timeout=timeout):
        raise TimeoutError("Chromecast media status refresh timed out")
    return True


def _wait_for_volume_muted(cast, expected_muted: bool) -> None:
    _wait_until(
        read=lambda: getattr(cast.status, "volume_muted", None),
        matches=lambda muted: muted == expected_muted,
        refresh=lambda: _refresh_receiver_status(cast),
        timeout=VOLUME_CONFIRM_TIMEOUT_SECONDS,
        interval=VOLUME_CONFIRM_INTERVAL_SECONDS,
        timeout_message=lambda muted: (
            "Chromecast muted state did not become "
            f"{expected_muted}; last reported muted state was {muted}"
        ),
    )


def _wait_for_media_loaded(media, expected_url: str) -> None:
    _wait_until(
        read=lambda: getattr(media.status, "content_id", None),
        matches=lambda content_id: content_id == expected_url,
        refresh=lambda: _refresh_media_status(media, timeout=0.5),
        timeout=MEDIA_LOAD_CONFIRM_TIMEOUT_SECONDS,
        interval=MEDIA_LOAD_CONFIRM_INTERVAL_SECONDS,
        timeout_message=lambda content_id: (
            "Chromecast media did not load expected content "
            f"{expected_url!r}; last content ID was {content_id!r}"
        ),
    )


def _wait_until(*, read, matches, refresh, timeout, interval, timeout_message) -> None:
    deadline = time.monotonic() + timeout
    last_value = read()

    while time.monotonic() < deadline:
        if matches(last_value):
            return

        refresh()
        last_value = read()
        if matches(last_value):
            return

        time.sleep(interval)

    raise TimeoutError(timeout_message(last_value))

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
