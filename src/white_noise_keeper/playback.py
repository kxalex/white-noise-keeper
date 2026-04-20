from __future__ import annotations

import logging
from collections.abc import Callable

from .cast import CastClient, CastState, expected_media_loaded

LOG = logging.getLogger(__name__)
MEDIA_END_RELOAD_THRESHOLD_SECONDS = 60.0
MIN_RESTORED_VOLUME_LEVEL = 0.80


class AudioLoadGuard:
    def __init__(self, cast: CastClient):
        self.cast = cast
        self._pending_restore = False
        self._pending_volume_level: float | None = None

    def load(self, state: CastState, autoplay: bool) -> None:
        self._pending_restore = True
        self._pending_volume_level = _restorable_volume_level(state.volume_level)
        try:
            LOG.info(
                "Muting Chromecast for load; current volume is %s; current media: %s",
                _format_optional_volume(self._pending_volume_level),
                _format_current_media(state),
            )
            self.cast.set_muted(True)
            self.cast.load(autoplay=autoplay)
            self._restore()
            self._clear_pending()
        except Exception:
            if self._restore_best_effort():
                self._clear_pending()
            raise

    def restore_pending(self) -> bool:
        if not self._pending_restore:
            return False
        LOG.info("Restoring pending Chromecast audio state before next action")
        self._restore()
        self._clear_pending()
        return True

    def _restore(self) -> None:
        if self._pending_volume_level is not None:
            LOG.info(
                "Restoring Chromecast volume to %.2f",
                self._pending_volume_level,
            )
            self.cast.set_volume_level(self._pending_volume_level)
        LOG.info("Restoring Chromecast muted state to False")
        self.cast.set_muted(False)

    def _restore_best_effort(self) -> bool:
        try:
            self._restore()
        except Exception as restore_exc:
            LOG.warning(
                "Failed to restore Chromecast audio state after failed load: %s",
                restore_exc,
            )
            try:
                self.cast.set_muted(False)
            except Exception as mute_restore_exc:
                LOG.warning(
                    "Failed to restore Chromecast muted state after failed load: %s",
                    mute_restore_exc,
                )
            return False
        return True

    def _clear_pending(self) -> None:
        self._pending_restore = False
        self._pending_volume_level = None


class WhiteNoisePlayback:
    def __init__(
        self,
        cast: CastClient,
        expected_url: str,
        on_state: Callable[[CastState], None] | None = None,
    ):
        self.cast = cast
        self.expected_url = expected_url
        self.on_state = on_state
        self.audio_load_guard = AudioLoadGuard(cast)

    def ensure_playing(self) -> CastState:
        state = self.ensure_loaded(autoplay=True)
        if not state.playing:
            LOG.info("Expected media is loaded but not playing; sending play")
            self.cast.play()
            return self._get_state()
        return state

    def ensure_loaded(self, autoplay: bool) -> CastState:
        state = self._get_state()
        if self.audio_load_guard.restore_pending():
            state = self._get_state()
        if expected_media_loaded(state, self.expected_url):
            if _near_media_end(state):
                reload_autoplay = state.playing
                if reload_autoplay:
                    LOG.info("Expected media is near the end; reloading and playing")
                else:
                    LOG.info("Expected media is near the end; reloading paused")
                self.audio_load_guard.load(state, autoplay=reload_autoplay)
                state = self._get_state()
            return state

        if autoplay:
            LOG.info("Expected media is not loaded; loading and playing")
        else:
            LOG.info("Expected media is not loaded; loading paused")
        self.audio_load_guard.load(state, autoplay=autoplay)
        return self._get_state()

    def load_from_beginning_paused(self) -> None:
        LOG.info("Loading white noise paused from the beginning")
        state = self._get_state()
        self.audio_load_guard.load(state, autoplay=False)
        self._get_state()

    def is_expected_playing(self, state: CastState) -> bool:
        return expected_media_loaded(state, self.expected_url) and state.playing

    def _get_state(self) -> CastState:
        state = self.cast.get_state()
        if self.on_state is not None:
            self.on_state(state)
        return state


def _near_media_end(state: CastState) -> bool:
    if state.current_time is None or state.duration is None:
        return False
    if state.duration <= 0:
        return False
    return state.duration - state.current_time <= MEDIA_END_RELOAD_THRESHOLD_SECONDS


def _format_optional_volume(volume: float | None) -> str:
    if volume is None:
        return "unknown"
    return f"{volume:.2f}"


def _format_current_media(state: CastState) -> str:
    if state.content_id is None:
        return "Idle"
    if state.player_state is None:
        return state.content_id
    return f"{state.content_id} ({state.player_state})"


def _restorable_volume_level(volume: float | None) -> float | None:
    if volume == 0:
        return MIN_RESTORED_VOLUME_LEVEL
    return volume
