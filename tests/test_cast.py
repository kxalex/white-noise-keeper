import unittest
from types import SimpleNamespace
from unittest.mock import patch

from white_noise_keeper.cast import _wait_for_volume_level


class CastVolumeTest(unittest.TestCase):
    def test_wait_for_volume_level_refreshes_receiver_status_until_matched(self):
        cast = fake_cast(volume_level=0.9)

        def update_status(callback_function=None):
            cast.status.volume_level = 0.0
            if callback_function is not None:
                callback_function(True, {})

        cast.socket_client.receiver_controller.update_status = update_status

        _wait_for_volume_level(cast, 0.0)

        self.assertEqual(cast.status.volume_level, 0.0)

    def test_wait_for_volume_level_times_out_when_status_never_matches(self):
        cast = fake_cast(volume_level=0.9)

        with patch("white_noise_keeper.cast.VOLUME_CONFIRM_TIMEOUT_SECONDS", 0.01):
            with patch("white_noise_keeper.cast.VOLUME_CONFIRM_INTERVAL_SECONDS", 0.0):
                with self.assertRaises(TimeoutError):
                    _wait_for_volume_level(cast, 0.0)


def fake_cast(volume_level):
    receiver_controller = SimpleNamespace(update_status=lambda callback_function=None: None)
    return SimpleNamespace(
        status=SimpleNamespace(volume_level=volume_level),
        socket_client=SimpleNamespace(receiver_controller=receiver_controller),
    )


if __name__ == "__main__":
    unittest.main()
