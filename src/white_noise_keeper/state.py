from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)


@dataclass
class RuntimeState:
    ipad_backup_active: bool = False
    last_ipad_play_triggered_at: float | None = None
    nest_failure_started_at: float | None = None
    nest_recovered_started_at: float | None = None
    force_start_active: bool = False
    force_start_until: float | None = None
    suppressed_until: float | None = None
    last_command: dict | None = None
    last_cast_state: dict | None = None


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            with self.path.open("r", encoding="utf-8") as state_file:
                data = json.load(state_file)
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("Could not load runtime state from %s: %s", self.path, exc)
            return RuntimeState()
        return RuntimeState(
            ipad_backup_active=bool(data.get("ipad_backup_active", False)),
            last_ipad_play_triggered_at=_optional_float(
                data.get("last_ipad_play_triggered_at")
            ),
            nest_failure_started_at=_optional_float(data.get("nest_failure_started_at")),
            nest_recovered_started_at=_optional_float(
                data.get("nest_recovered_started_at")
            ),
            force_start_active=bool(data.get("force_start_active", False)),
            force_start_until=_optional_float(data.get("force_start_until")),
            suppressed_until=_optional_float(data.get("suppressed_until")),
            last_command=_optional_dict(data.get("last_command")),
            last_cast_state=_optional_dict(data.get("last_cast_state")),
        )

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as state_file:
            json.dump(asdict(state), state_file, indent=2, sort_keys=True)
            state_file.write("\n")
        temp_path.replace(self.path)


def _optional_float(value):
    if value is None:
        return None
    return float(value)


def _optional_dict(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return None
