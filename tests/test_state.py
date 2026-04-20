import tempfile
import unittest
from pathlib import Path

from white_noise_keeper.state import RuntimeState, StateStore


class StateStoreTest(unittest.TestCase):
    def test_manual_mode_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            store = StateStore(state_path)

            store.save(RuntimeState(manual_mode="suppress", manual_until=123.0))
            state = store.load()

        self.assertEqual(state.manual_mode, "suppress")
        self.assertEqual(state.manual_until, 123.0)


if __name__ == "__main__":
    unittest.main()
