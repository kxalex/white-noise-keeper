import tempfile
import unittest
from pathlib import Path

from white_noise_keeper.state import RuntimeState, StateStore


class StateStoreTest(unittest.TestCase):
    def test_last_cast_state_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            store = StateStore(state_path)

            snapshot = {
                "content_id": "http://example.local/white-noise.mp4",
                "player_state": "PLAYING",
                "current_time": 12.5,
                "duration": 3600.0,
                "volume_muted": False,
                "volume_level": 0.77,
            }
            store.save(RuntimeState(last_cast_state=snapshot))
            state = store.load()

        self.assertEqual(state.last_cast_state, snapshot)

    def test_stats_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            store = StateStore(state_path)
            stats = {
                "open_outage": {"started_at": 100.0, "reason": "nest_unavailable"},
                "failure_records": [
                    {
                        "started_at": 10.0,
                        "ended_at": 20.0,
                        "reason": "nest_unavailable",
                        "duration_seconds": 10.0,
                    }
                ],
            }

            store.save(RuntimeState(stats=stats))
            state = store.load()

        self.assertEqual(state.stats, stats)

    def test_loads_state_without_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                "{\n  \"last_cast_state\": {\"player_state\": \"PLAYING\"}\n}\n",
                encoding="utf-8",
            )
            store = StateStore(state_path)

            state = store.load()

        self.assertIsNone(state.stats)

    def test_save_skips_unchanged_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            store = StateStore(state_path)
            state = RuntimeState(last_cast_state={"player_state": "PLAYING"})

            store.save(state)
            first_contents = state_path.read_text(encoding="utf-8")
            store.save(state)
            second_contents = state_path.read_text(encoding="utf-8")

        self.assertEqual(first_contents, second_contents)


if __name__ == "__main__":
    unittest.main()
