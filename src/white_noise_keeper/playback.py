from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .cast import (
    CastClient,
    CastState,
    PLAYER_BUFFERING,
    PLAYER_PLAYING,
    expected_media_loaded,
)

LOG = logging.getLogger(__name__)
MEDIA_END_RELOAD_THRESHOLD_SECONDS = 60.0
MUTE_AFTER_LOAD_DELAY_SECONDS = 1.0


class AudioLoadGuard:
    def __init__(self, cast: CastClient, sleep: Callable[[float], None] = time.sleep):
        self.cast = cast
        self.sleep = sleep
        self._pending_restore = False
        self._target_muted = False

    def load(self, state: CastState, autoplay: bool, muted: bool | None = None) -> None:
        self._target_muted = _normalize_muted(
            state.volume_muted if muted is None else muted
        )
        self._pending_restore = True
        try:
            LOG.info(
                "Muting Chromecast for load; current volume is %s; current media: %s",
                _format_optional_volume(state.volume_level),
                _format_current_media(state),
            )
            self.cast.set_muted(True)
            self.cast.load(autoplay=autoplay)
            LOG.info(
                "Waiting %.1fs after load before unmuting to avoid Nest beep.",
                MUTE_AFTER_LOAD_DELAY_SECONDS,
            )
            self.sleep(MUTE_AFTER_LOAD_DELAY_SECONDS)
            self._restore_target_muted()
            self._clear_pending()
        except Exception:
            if self._restore_best_effort():
                self._clear_pending()
            raise

    def restore_pending(self) -> bool:
        if not self._pending_restore:
            return False
        LOG.info("Restoring pending Chromecast mute state before next action")
        return self.restore_target_muted()

    def has_pending_restore(self) -> bool:
        return self._pending_restore

    def restore_target_muted(self, muted: bool | None = None) -> bool:
        if muted is not None:
            self._target_muted = muted
        self._pending_restore = True
        try:
            self._restore_target_muted()
        except Exception as restore_exc:
            LOG.warning(
                "Failed to restore Chromecast mute state: %s",
                restore_exc,
            )
            return False
        self._clear_pending()
        return True

    def _restore_target_muted(self) -> None:
        LOG.info("Restoring Chromecast muted state to %s", self._target_muted)
        self.cast.set_muted(self._target_muted)

    def _restore_best_effort(self) -> bool:
        try:
            self._restore_target_muted()
        except Exception as restore_exc:
            LOG.warning(
                "Failed to restore Chromecast mute state after failed load: %s",
                restore_exc,
            )
            return False
        return True

    def _clear_pending(self) -> None:
        self._pending_restore = False


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
            state = self._get_state()
        if state.volume_muted:
            self.audio_load_guard.restore_target_muted(False)
            state = self._get_state()
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
                self.audio_load_guard.load(
                    state,
                    autoplay=reload_autoplay,
                    muted=state.volume_muted,
                )
                state = self._get_state()
            return state

        if autoplay:
            LOG.info("Expected media is not loaded; loading and playing")
        else:
            LOG.info("Expected media is not loaded; loading paused")
        self.audio_load_guard.load(state, autoplay=autoplay, muted=state.volume_muted)
        return self._get_state()

    def pause_at_beginning(self) -> None:
        state = self._get_state()
        if state.content_id is None:
            LOG.info("No media is loaded; nothing to pause")
            return

        LOG.info("Pausing media at beginning: %s", _format_current_media(state))
        self.cast.seek_to_start()
        self.cast.pause()
        self._get_state()

    def restore_snapshot(self, snapshot: dict) -> CastState:
        desired_url = snapshot.get("content_id")
        desired_playing = snapshot.get("player_state") in {PLAYER_PLAYING, PLAYER_BUFFERING}
        desired_muted = _normalize_muted(snapshot.get("volume_muted"))

        state = self._get_state()
        if desired_url is not None and state.content_id != desired_url:
            self.audio_load_guard.load(
                state,
                autoplay=desired_playing,
                muted=desired_muted,
            )
            state = self._get_state()

        if desired_playing:
            if not state.playing:
                LOG.info("Restoring expected media to playing state")
                self.cast.play()
                state = self._get_state()
        elif state.playing:
            LOG.info("Restoring expected media to paused state")
            self.pause_at_beginning()
            state = self._get_state()

        if state.volume_muted != desired_muted:
            if not self.audio_load_guard.restore_target_muted(desired_muted):
                raise RuntimeError("Chromecast mute restore failed")
        return self._get_state()

    def current_state(self) -> CastState:
        return self._get_state()

    def restore_pending(self) -> bool:
        return self.audio_load_guard.restore_pending()

    def has_pending_restore(self) -> bool:
        return self.audio_load_guard.has_pending_restore()

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


def _normalize_muted(value: bool | None) -> bool:
    return bool(value) if value is not None else False
