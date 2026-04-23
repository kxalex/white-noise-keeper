from __future__ import annotations

import datetime
import logging
import threading
import time
from dataclasses import dataclass

from .cast import CastClient, CastState
from .config import AppConfig
from .playback import WhiteNoisePlayback
from .state import RuntimeState, StateStore
from .systemd import SystemdNotifier, start_watchdog_heartbeat

LOG = logging.getLogger("white_noise_keeper.keeper")


@dataclass(frozen=True)
class KeeperResult:
    healthy: bool
    message: str


class WhiteNoiseKeeper:
    def __init__(
        self,
        config: AppConfig,
        cast_client: CastClient,
        state_store: StateStore,
        notifier: SystemdNotifier | None = None,
        clock=time.time,
    ):
        self.config = config
        self.cast = cast_client
        self.state_store = state_store
        self.notifier = notifier or SystemdNotifier()
        self.clock = clock
        self.state = state_store.load()
        self._published_state = _copy_runtime_state(self.state)
        self.playback = WhiteNoisePlayback(
            cast_client,
            config.cast.url,
            on_state=self._remember_cast_state,
        )
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
        start_watchdog_heartbeat(self.notifier)
        threading.Thread(
            target=self._run_daily_start_scheduler,
            name="white-noise-keeper-scheduler",
            daemon=True,
        ).start()
        failure_count = 0
        while True:
            try:
                result = self.run_once()
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                LOG.exception("Keeper loop failed")
                result = KeeperResult(healthy=False, message=f"Keeper loop failed: {exc}")
            self.notifier.status(result.message)
            if result.healthy:
                failure_count = 0
                sleep_seconds = self.config.monitor.interval_seconds
            else:
                failure_count += 1
                sleep_seconds = _retry_sleep_seconds(
                    self.config.monitor.interval_seconds,
                    failure_count,
                )
            time.sleep(sleep_seconds)

    def run_once(self) -> KeeperResult:
        with self._lock:
            if not self.playback.restore_pending() and self.playback.has_pending_restore():
                return KeeperResult(healthy=False, message="Cast mute restore pending")
            try:
                current = self.playback.current_state()
            except Exception:
                LOG.warning("Cast health check failed; resetting Chromecast connection")
                LOG.debug("Cast health check error", exc_info=True)
                self.cast.reset()
                snapshot = self._saved_media_snapshot()
                if snapshot is None:
                    return KeeperResult(
                        healthy=False,
                        message="Nest unavailable; retrying",
                    )
                try:
                    current = self.playback.restore_snapshot(snapshot)
                    LOG.info("Cast restore succeeded; connection recovered")
                except Exception:
                    LOG.warning("Cast restore failed; retrying")
                    LOG.debug("Cast restore error", exc_info=True)
                    return KeeperResult(
                        healthy=False,
                        message="Nest restore failed; retrying",
                    )

            if current.content_id != self.config.cast.url:
                snapshot = self._saved_media_snapshot()
                if snapshot is not None:
                    LOG.info("Cast media differs; restoring last successful media state")
                    try:
                        current = self.playback.restore_snapshot(snapshot)
                        LOG.info("Cast restore succeeded; connection recovered")
                    except Exception:
                        LOG.warning("Cast restore failed; retrying")
                        LOG.debug("Cast restore error", exc_info=True)
                        return KeeperResult(
                            healthy=False,
                            message="Nest restore failed; retrying",
                        )
                else:
                    LOG.info(
                        "Cast media differs and no saved state exists; loading paused"
                    )
                    try:
                        current = self.playback.ensure_loaded(autoplay=False)
                    except Exception:
                        LOG.warning("Cast preload failed; retrying")
                        LOG.debug("Cast preload error", exc_info=True)
                        return KeeperResult(
                            healthy=False,
                            message="Nest unavailable; retrying",
                        )

            self._store_cast_state(current)
            self.state_store.save(self.state)
            self._publish_state()
            return KeeperResult(healthy=True, message=_state_message(current))

    def command_start(self) -> dict:
        return self._run_command("start", self.playback.ensure_playing)

    def command_stop(self) -> dict:
        return self._run_command("stop", self.playback.pause_at_beginning)

    def status_snapshot(self) -> dict:
        return {
            "ok": True,
            "last_command": _copy_optional_dict(self._published_state.last_command),
            "last_cast_state": _copy_optional_dict(self._published_state.last_cast_state),
        }

    def _run_command(self, action: str, runner) -> dict:
        with self._lock:
            if not self.playback.restore_pending() and self.playback.has_pending_restore():
                raise RuntimeError("Cast mute restore pending")
            runner()
            current = self.playback.current_state()
            self._store_cast_state(current)
            self._record_command(action)
            self.state_store.save(self.state)
            self._publish_state()
            return {
                "ok": True,
                "last_command": self.state.last_command,
                "last_cast_state": self.state.last_cast_state,
            }

    def _remember_cast_state(self, state: CastState) -> None:
        self._store_cast_state(state)

    def _store_cast_state(self, state: CastState) -> None:
        snapshot = self._snapshot(state)
        if snapshot["content_id"] != self.config.cast.url:
            return
        if snapshot != self.state.last_cast_state:
            self.state.last_cast_state = snapshot

    def _snapshot(self, state: CastState) -> dict:
        return {
            "content_id": state.content_id,
            "player_state": state.player_state,
            "current_time": state.current_time,
            "duration": state.duration,
            "volume_muted": state.volume_muted,
            "volume_level": state.volume_level,
        }

    def _record_command(self, action: str) -> None:
        self.state.last_command = {
            "action": action,
            "timestamp": self.clock(),
        }

    def _publish_state(self) -> None:
        self._published_state = _copy_runtime_state(self.state)

    def _saved_media_snapshot(self) -> dict | None:
        snapshot = self.state.last_cast_state
        if snapshot is None:
            return None
        if snapshot.get("content_id") is None:
            return None
        return snapshot

    def _run_daily_start_scheduler(self) -> None:
        while True:
            delay_seconds = _seconds_until_next_eight_pm(self.clock())
            time.sleep(delay_seconds)
            try:
                LOG.info("8pm reached; starting white noise")
                self.command_start()
            except Exception as exc:  # pragma: no cover - scheduler is best effort
                LOG.warning("Scheduled start failed: %s", exc)

def _state_message(state: CastState) -> str:
    if state.content_id is None:
        return "Nest is idle"
    if state.playing:
        return "Nest is playing white noise"
    return "Nest is paused"


def _retry_sleep_seconds(base_interval: float, failure_count: int) -> float:
    if failure_count <= 0:
        return base_interval

    cap = max(base_interval, 10.0)
    exponent = min(failure_count - 1, 4)
    return min(base_interval * (2**exponent), cap)


def _copy_runtime_state(state: RuntimeState) -> RuntimeState:
    return RuntimeState(
        last_cast_state=_copy_optional_dict(state.last_cast_state),
        last_command=_copy_optional_dict(state.last_command),
    )


def _seconds_until_next_eight_pm(now_seconds: float) -> float:
    now = datetime.datetime.fromtimestamp(now_seconds)
    next_run = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if now >= next_run:
        next_run += datetime.timedelta(days=1)
    return max(0.0, (next_run - now).total_seconds())


def _copy_optional_dict(value: dict | None) -> dict | None:
    if value is None:
        return None
    return dict(value)
