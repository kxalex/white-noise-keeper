from __future__ import annotations

from dataclasses import MISSING, dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - for developer machines below 3.11
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class CastConfig:
    name: str
    url: str
    content_type: str = "video/mp4"
    discovery_timeout_seconds: float = 10.0
    known_hosts: tuple[str, ...] = ()


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
class AppConfig:
    cast: CastConfig
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    http: HttpConfig = field(default_factory=HttpConfig)


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as config_file:
        raw = tomllib.load(config_file)
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> AppConfig:
    cast_raw = raw.get("cast", MISSING)
    monitor_raw = raw.get("monitor", {})
    http_raw = raw.get("http", {})

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
    if monitor.interval_seconds <= 0:
        raise ValueError("monitor.interval_seconds must be greater than zero")
    if http.port <= 0 or http.port > 65535:
        raise ValueError("http.port must be between 1 and 65535")

    return AppConfig(
        cast=cast,
        monitor=monitor,
        http=http,
    )


def required_string(raw: dict[str, Any], key: str, message: str) -> str:
    value = raw.get(key, MISSING)
    if value is MISSING or str(value).strip() == "":
        raise ValueError(message)
    return str(value)
