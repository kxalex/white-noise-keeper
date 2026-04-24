from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)


@dataclass
class RuntimeState:
    last_cast_state: dict | None = None
    last_command: dict | None = None
    stats: dict | None = None


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._last_serialized: str | None = None

    def load(self) -> RuntimeState:
        if not self.path.exists():
            self._last_serialized = None
            return RuntimeState()
        try:
            with self.path.open("r", encoding="utf-8") as state_file:
                data = json.load(state_file)
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("Could not load runtime state from %s: %s", self.path, exc)
            self._last_serialized = None
            return RuntimeState()
        state = RuntimeState(
            last_cast_state=_optional_dict(data.get("last_cast_state")),
            last_command=_optional_dict(data.get("last_command")),
            stats=_optional_dict(data.get("stats")),
        )
        self._last_serialized = self._serialize(state)
        return state

    def save(self, state: RuntimeState) -> None:
        serialized = self._serialize(state)
        if serialized == self._last_serialized:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as state_file:
            state_file.write(serialized)
        temp_path.replace(self.path)
        self._last_serialized = serialized

    def _serialize(self, state: RuntimeState) -> str:
        data = {
            "last_cast_state": state.last_cast_state,
            "last_command": state.last_command,
            "stats": state.stats,
        }
        return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _optional_dict(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return None
