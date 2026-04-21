import tempfile
import unittest
from pathlib import Path

from white_noise_keeper.state import RuntimeState, StateStore


class StateStoreTest(unittest.TestCase):
    def test_force_state_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            store = StateStore(state_path)

            store.save(RuntimeState(force_enabled=True, last_active_window=False))
            state = store.load()

        self.assertTrue(state.force_enabled)
        self.assertFalse(state.last_active_window)


if __name__ == "__main__":
    unittest.main()
