from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from .cast import CastClient, CastState, expected_media_loaded
from .config import AppConfig
from .pushcut import PushcutClient
from .state import RuntimeState, StateStore
from .systemd import SystemdNotifier
from .time_window import in_active_window

LOG = logging.getLogger(__name__)
MEDIA_END_RELOAD_THRESHOLD_SECONDS = 60.0


@dataclass(frozen=True)
class KeeperResult:
    healthy: bool
    active_window: bool
    message: str


class WhiteNoiseKeeper:
    def __init__(
        self,
        config: AppConfig,
        cast_client: CastClient,
        state_store: StateStore,
        pushcut_client: PushcutClient | None = None,
        notifier: SystemdNotifier | None = None,
        clock=time.time,
    ):
        self.config = config
        self.cast = cast_client
        self.state_store = state_store
        self.pushcut = pushcut_client
        self.notifier = notifier or SystemdNotifier()
        self.clock = clock
        self.state = state_store.load()
        self._lock = threading.RLock()
        self._api_server = None

    def run_forever(self) -> None:
        if self.config.http.enabled:
            from .api import start_api_server

            self._api_server = start_api_server(
                self,
                self.config.http.host,
                self.config.http.port,
            )
        self.notifier.ready()
        while True:
            result = self.run_once()
            self.notifier.status(result.message)
            if result.healthy:
                self.notifier.watchdog()
            time.sleep(self.config.monitor.interval_seconds)

    def run_once(self, now: datetime | None = None) -> KeeperResult:
        with self._lock:
            now = now or datetime.now().astimezone()
            timestamp = self.clock()
            active = self._active_window(now)
            self._expire_overrides(timestamp)
            suppressed = self.state.auto_start_suppressed
            force_active = self._force_start_active(timestamp)
            should_enforce_play = not suppressed and (active or force_active)

            try:
                if should_enforce_play:
                    return self._run_active_window(timestamp, active_window=active)
                return self._run_outside_window(
                    suppressed=suppressed,
                    active_window=active,
                )
            finally:
                self.state_store.save(self.state)

    def trigger_ipad_backup(self, dry_run: bool = False) -> None:
        with self._lock:
            if self.pushcut is None:
                raise RuntimeError("Pushcut is not configured")
            self.pushcut.trigger_play(dry_run=dry_run)
            if not dry_run:
                now = self.clock()
                self.state.ipad_backup_active = True
                self.state.last_ipad_play_triggered_at = now
                self.state.nest_recovered_started_at = None
                self.state_store.save(self.state)

    def stop_ipad_backup(self, dry_run: bool = False) -> None:
        with self._lock:
            if self.pushcut is None:
                raise RuntimeError("Pushcut is not configured")
            self.pushcut.trigger_stop(dry_run=dry_run)
            if not dry_run:
                self._mark_ipad_stopped()
                self.state_store.save(self.state)

    def command_start(self) -> dict:
        with self._lock:
            self.state.auto_start_suppressed = False
            self.state.force_start_until = None
            self._ensure_playing()
            self._record_command("start")
            self.state_store.save(self.state)
            return self.status_snapshot()

    def command_start_force(self) -> dict:
        with self._lock:
            now = datetime.now().astimezone()
            self.state.auto_start_suppressed = False
            self.state.force_start_until = self._next_active_end(now).timestamp()
            self._ensure_playing()
            self._record_command("start-force")
            self.state_store.save(self.state)
            return self.status_snapshot()

    def command_stop(self) -> dict:
        with self._lock:
            self.state.force_start_until = None
            self.state.auto_start_suppressed = True
            self._load_from_beginning_paused()
            self._record_command("stop")
            self.state_store.save(self.state)
            return self.status_snapshot()

    def status_snapshot(self) -> dict:
        with self._lock:
            now = datetime.now().astimezone()
            timestamp = self.clock()
            active = self._active_window(now)
            self._expire_overrides(timestamp)
            suppressed = self.state.auto_start_suppressed
            force_active = self._force_start_active(timestamp)
            effective_active = not suppressed and (active or force_active)
            self.state_store.save(self.state)
            return {
                "ok": True,
                "active_window": active,
                "effective_active": effective_active,
                "force_start_active": force_active,
                "force_start_until": self.state.force_start_until,
                "auto_start_suppressed": self.state.auto_start_suppressed,
                "suppressed": suppressed,
                "suppressed_until": None,
                "schedule": {
                    "active_start": self.config.schedule.active_start,
                    "active_end": self.config.schedule.active_end,
                },
                "last_command": self.state.last_command,
                "last_cast_state": self.state.last_cast_state,
            }

    def _run_active_window(self, timestamp: float, active_window: bool) -> KeeperResult:
        try:
            cast_state = self._ensure_playing()
        except Exception as exc:
            LOG.warning("Nest recovery attempt failed: %s", exc)
            self._record_nest_failure(timestamp)
            self._maybe_trigger_ipad_backup(timestamp)
            return KeeperResult(
                healthy=False,
                active_window=active_window,
                message=f"Nest recovery failed: {exc}",
            )

        healthy = self._is_expected_playing(cast_state)
        if healthy:
            self._record_nest_healthy(timestamp)
            self._maybe_stop_ipad_after_stable_recovery(timestamp)
            return KeeperResult(
                healthy=True,
                active_window=active_window,
                message="Nest is playing white noise",
            )

        self._record_nest_failure(timestamp)
        self._maybe_trigger_ipad_backup(timestamp)
        return KeeperResult(
            healthy=False,
            active_window=active_window,
            message="Nest is not playing expected media",
        )

    def _run_outside_window(
        self,
        suppressed: bool,
        active_window: bool,
    ) -> KeeperResult:
        try:
            self._ensure_loaded(autoplay=False)
            healthy = True
            if suppressed:
                message = "Nest auto-start is suppressed"
            else:
                message = "Nest has white noise loaded"
        except Exception as exc:
            LOG.warning("Nest preload attempt failed: %s", exc)
            healthy = False
            message = f"Nest preload failed: {exc}"

        self.state.nest_failure_started_at = None
        self.state.nest_recovered_started_at = None

        if not active_window and self.state.ipad_backup_active:
            if self._stop_ipad_due_to_window_end():
                message = "Active window ended; iPad backup stopped"
                healthy = True
            else:
                message = "Active window ended; iPad backup stop failed"
                healthy = False

        return KeeperResult(healthy=healthy, active_window=active_window, message=message)

    def _ensure_playing(self) -> CastState:
        state = self._ensure_loaded(autoplay=True)
        if not state.playing:
            LOG.info("Expected media is loaded but not playing; sending play")
            self.cast.play()
            state = self.cast.get_state()
            self._remember_cast_state(state)
            return state
        return state

    def _ensure_loaded(self, autoplay: bool) -> CastState:
        state = self.cast.get_state()
        self._remember_cast_state(state)
        if expected_media_loaded(state, self.config.cast.url):
            if self._near_media_end(state):
                reload_autoplay = state.playing
                if reload_autoplay:
                    LOG.info("Expected media is near the end; reloading and playing")
                else:
                    LOG.info("Expected media is near the end; reloading paused")
                self._reload_with_temporary_mute(state, autoplay=reload_autoplay)
                state = self.cast.get_state()
                self._remember_cast_state(state)
            return state

        if autoplay:
            LOG.info("Expected media is not loaded; loading and playing")
        else:
            LOG.info("Expected media is not loaded; loading paused")
        self.cast.load(autoplay=autoplay)
        state = self.cast.get_state()
        self._remember_cast_state(state)
        return state

    def _load_from_beginning_paused(self) -> None:
        LOG.info("Loading white noise paused from the beginning")
        self.cast.load(autoplay=False)
        state = self.cast.get_state()
        self._remember_cast_state(state)

    def _is_expected_playing(self, state: CastState) -> bool:
        return expected_media_loaded(state, self.config.cast.url) and state.playing

    def _reload_with_temporary_mute(self, state: CastState, autoplay: bool) -> None:
        muted_before_reload = state.volume_muted
        should_restore_unmuted = muted_before_reload is False
        if should_restore_unmuted:
            self.cast.set_muted(True)
        try:
            self.cast.load(autoplay=autoplay)
        finally:
            if should_restore_unmuted:
                self.cast.set_muted(False)

    def _near_media_end(self, state: CastState) -> bool:
        if state.current_time is None or state.duration is None:
            return False
        if state.duration <= 0:
            return False
        return (
            state.duration - state.current_time
            <= MEDIA_END_RELOAD_THRESHOLD_SECONDS
        )

    def _record_nest_failure(self, timestamp: float) -> None:
        if self.state.nest_failure_started_at is None:
            self.state.nest_failure_started_at = timestamp
        self.state.nest_recovered_started_at = None

    def _record_nest_healthy(self, timestamp: float) -> None:
        self.state.nest_failure_started_at = None
        if self.state.ipad_backup_active and self.state.nest_recovered_started_at is None:
            LOG.info("Nest restored; starting iPad stop stability timer")
            self.state.nest_recovered_started_at = timestamp
        elif not self.state.ipad_backup_active:
            self.state.nest_recovered_started_at = None

    def _maybe_trigger_ipad_backup(self, timestamp: float) -> None:
        backup = self.config.ipad_backup
        if not backup.enabled or self.pushcut is None:
            return
        if self.state.ipad_backup_active:
            return
        if self.state.nest_failure_started_at is None:
            return
        failure_age = timestamp - self.state.nest_failure_started_at
        if failure_age < backup.trigger_after_failure_seconds:
            return
        if self.state.last_ipad_play_triggered_at is not None:
            cooldown_age = timestamp - self.state.last_ipad_play_triggered_at
            if cooldown_age < backup.retrigger_cooldown_seconds:
                return

        LOG.warning("Triggering iPad backup after %.1fs Nest failure", failure_age)
        try:
            self.pushcut.trigger_play()
        except Exception as exc:
            LOG.warning("Could not trigger iPad backup: %s", exc)
            self.state.last_ipad_play_triggered_at = timestamp
            return
        self.state.ipad_backup_active = True
        self.state.last_ipad_play_triggered_at = timestamp
        self.state.nest_recovered_started_at = None

    def _maybe_stop_ipad_after_stable_recovery(self, timestamp: float) -> None:
        backup = self.config.ipad_backup
        if not backup.enabled or self.pushcut is None:
            return
        if not self.state.ipad_backup_active:
            return
        if self.state.nest_recovered_started_at is None:
            self.state.nest_recovered_started_at = timestamp
            return
        recovered_age = timestamp - self.state.nest_recovered_started_at
        if recovered_age < backup.stop_after_recovered_seconds:
            return

        LOG.info("Stopping iPad backup after %.1fs stable Nest recovery", recovered_age)
        try:
            self.pushcut.trigger_stop()
        except Exception as exc:
            LOG.warning("Could not stop iPad backup: %s", exc)
            return
        self._mark_ipad_stopped()

    def _stop_ipad_due_to_window_end(self) -> bool:
        if self.config.ipad_backup.enabled and self.pushcut is not None:
            LOG.info("Stopping iPad backup because active window ended")
            try:
                self.pushcut.trigger_stop()
            except Exception as exc:
                LOG.warning("Could not stop iPad backup after active window ended: %s", exc)
                return False
        self._mark_ipad_stopped()
        return True

    def _mark_ipad_stopped(self) -> None:
        self.state.ipad_backup_active = False
        self.state.nest_recovered_started_at = None

    def _active_window(self, now: datetime) -> bool:
        return in_active_window(
            now.time(),
            self.config.schedule.active_start_time,
            self.config.schedule.active_end_time,
        )

    def _expire_overrides(self, timestamp: float) -> None:
        if self.state.force_start_until is not None and timestamp >= self.state.force_start_until:
            self.state.force_start_until = None

    def _force_start_active(self, timestamp: float) -> bool:
        return (
            self.state.force_start_until is not None
            and timestamp < self.state.force_start_until
        )

    def _next_active_end(self, now: datetime) -> datetime:
        end = self.config.schedule.active_end_time
        candidate = now.replace(
            hour=end.hour,
            minute=end.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _record_command(self, action: str) -> None:
        self.state.last_command = {
            "action": action,
            "timestamp": self.clock(),
        }

    def _remember_cast_state(self, state: CastState) -> None:
        self.state.last_cast_state = {
            "content_id": state.content_id,
            "player_state": state.player_state,
            "current_time": state.current_time,
            "duration": state.duration,
            "volume_muted": state.volume_muted,
            "volume_level": state.volume_level,
        }
