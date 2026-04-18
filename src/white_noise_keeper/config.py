from __future__ import annotations

from dataclasses import MISSING, dataclass, field
from pathlib import Path
from typing import Any

from .time_window import parse_hhmm

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - for developer machines below 3.11
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class CastConfig:
    name: str
    url: str
    content_type: str = "video/mp4"
    discovery_timeout_seconds: float = 30.0
    known_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScheduleConfig:
    active_start: str = "20:00"
    active_end: str = "08:00"

    @property
    def active_start_time(self):
        return parse_hhmm(self.active_start)

    @property
    def active_end_time(self):
        return parse_hhmm(self.active_end)


@dataclass(frozen=True)
class MonitorConfig:
    interval_seconds: float = 5.0
    state_path: Path = Path("/var/lib/white-noise-keeper/state.json")


@dataclass(frozen=True)
class HttpConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8765


@dataclass(frozen=True)
class IpadBackupConfig:
    enabled: bool = False
    play_url: str = ""
    stop_url: str = ""
    trigger_after_failure_seconds: float = 30.0
    retrigger_cooldown_seconds: float = 1800.0
    stop_after_recovered_seconds: float = 600.0


@dataclass(frozen=True)
class AppConfig:
    cast: CastConfig
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    ipad_backup: IpadBackupConfig = field(default_factory=IpadBackupConfig)


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as config_file:
        raw = tomllib.load(config_file)
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> AppConfig:
    cast_raw = raw.get("cast", MISSING)
    schedule_raw = raw.get("schedule", {})
    monitor_raw = raw.get("monitor", {})
    http_raw = raw.get("http", {})
    ipad_raw = raw.get("ipad_backup", {})

    if cast_raw is MISSING or not isinstance(cast_raw, dict):
        raise ValueError("cast section is required")
    cast_name = required_string(cast_raw, "name", "cast.name is required")
    cast_url = required_string(cast_raw, "url", "cast.url is required")

    cast = CastConfig(
        name=cast_name,
        url=cast_url,
        content_type=str(cast_raw.get("content_type", CastConfig.content_type)),
        discovery_timeout_seconds=float(
            cast_raw.get(
                "discovery_timeout_seconds",
                CastConfig.discovery_timeout_seconds,
            )
        ),
        known_hosts=tuple(str(host) for host in cast_raw.get("known_hosts", ())),
    )
    schedule = ScheduleConfig(
        active_start=str(schedule_raw.get("active_start", ScheduleConfig.active_start)),
        active_end=str(schedule_raw.get("active_end", ScheduleConfig.active_end)),
    )
    monitor = MonitorConfig(
        interval_seconds=float(
            monitor_raw.get("interval_seconds", MonitorConfig.interval_seconds)
        ),
        state_path=Path(
            str(monitor_raw.get("state_path", MonitorConfig.state_path))
        ),
    )
    http = HttpConfig(
        enabled=bool(http_raw.get("enabled", HttpConfig.enabled)),
        host=str(http_raw.get("host", HttpConfig.host)),
        port=int(http_raw.get("port", HttpConfig.port)),
    )
    ipad_backup = IpadBackupConfig(
        enabled=bool(ipad_raw.get("enabled", IpadBackupConfig.enabled)),
        play_url=str(ipad_raw.get("play_url", IpadBackupConfig.play_url)),
        stop_url=str(ipad_raw.get("stop_url", IpadBackupConfig.stop_url)),
        trigger_after_failure_seconds=float(
            ipad_raw.get(
                "trigger_after_failure_seconds",
                IpadBackupConfig.trigger_after_failure_seconds,
            )
        ),
        retrigger_cooldown_seconds=float(
            ipad_raw.get(
                "retrigger_cooldown_seconds",
                IpadBackupConfig.retrigger_cooldown_seconds,
            )
        ),
        stop_after_recovered_seconds=float(
            ipad_raw.get(
                "stop_after_recovered_seconds",
                IpadBackupConfig.stop_after_recovered_seconds,
            )
        ),
    )

    schedule.active_start_time
    schedule.active_end_time
    if monitor.interval_seconds <= 0:
        raise ValueError("monitor.interval_seconds must be greater than zero")
    if http.port <= 0 or http.port > 65535:
        raise ValueError("http.port must be between 1 and 65535")
    if ipad_backup.trigger_after_failure_seconds < 0:
        raise ValueError("ipad_backup.trigger_after_failure_seconds cannot be negative")
    if ipad_backup.stop_after_recovered_seconds < 0:
        raise ValueError("ipad_backup.stop_after_recovered_seconds cannot be negative")
    if ipad_backup.enabled and (not ipad_backup.play_url or not ipad_backup.stop_url):
        raise ValueError("ipad_backup.play_url and stop_url are required when enabled")

    return AppConfig(
        cast=cast,
        schedule=schedule,
        monitor=monitor,
        http=http,
        ipad_backup=ipad_backup,
    )


def required_string(raw: dict[str, Any], key: str, message: str) -> str:
    value = raw.get(key, MISSING)
    if value is MISSING or str(value).strip() == "":
        raise ValueError(message)
    return str(value)
