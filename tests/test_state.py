import json
import tempfile
import unittest
from pathlib import Path

from white_noise_keeper.state import RuntimeState, StateStore


class StateStoreTest(unittest.TestCase):
    def test_old_state_fields_are_tolerated_but_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "force_start_active": True,
                        "force_start_until": 123.0,
                        "suppressed_until": 456.0,
                    }
                )
            )

            state = StateStore(state_path).load()

            StateStore(state_path).save(state)
            data = json.loads(state_path.read_text())

        self.assertEqual(state.force_start_until, 123.0)
        self.assertFalse(state.auto_start_suppressed)
        self.assertFalse(hasattr(state, "force_start_active"))
        self.assertFalse(hasattr(state, "suppressed_until"))
        self.assertEqual(data["force_start_until"], 123.0)
        self.assertNotIn("force_start_active", data)
        self.assertNotIn("suppressed_until", data)

    def test_auto_start_suppressed_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            store = StateStore(state_path)

            store.save(RuntimeState(auto_start_suppressed=True))
            state = store.load()

        self.assertTrue(state.auto_start_suppressed)


if __name__ == "__main__":
    unittest.main()
