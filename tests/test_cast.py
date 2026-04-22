import unittest
from types import SimpleNamespace
from unittest.mock import patch

from white_noise_keeper.cast import (
    PyChromecastClient,
    _refresh_media_status,
    _wait_for_media_loaded,
    _wait_for_volume_muted,
)
from white_noise_keeper.config import CastConfig


class CastVolumeTest(unittest.TestCase):
    def test_set_muted_waits_until_receiver_status_confirms_muted(self):
        cast = fake_cast(volume_muted=False)

        def update_status(callback_function=None):
            cast.status.volume_muted = True
            if callback_function is not None:
                callback_function(True, {})

        cast.socket_client.receiver_controller.update_status = update_status
        client = PyChromecastClient(
            CastConfig(name="Example", url="http://example.local")
        )
        client._cast = cast

        client.set_muted(True)

        self.assertEqual(cast.actions, [("set_volume_muted", True)])
        self.assertTrue(cast.status.volume_muted)

    def test_wait_for_volume_muted_refreshes_receiver_status_until_matched(self):
        cast = fake_cast(volume_muted=False)

        def update_status(callback_function=None):
            cast.status.volume_muted = True
            if callback_function is not None:
                callback_function(True, {})

        cast.socket_client.receiver_controller.update_status = update_status

        _wait_for_volume_muted(cast, True)

        self.assertTrue(cast.status.volume_muted)

    def test_wait_for_volume_muted_times_out_when_status_never_matches(self):
        cast = fake_cast(volume_muted=False)

        with patch("white_noise_keeper.cast.VOLUME_CONFIRM_TIMEOUT_SECONDS", 0.01):
            with patch("white_noise_keeper.cast.VOLUME_CONFIRM_INTERVAL_SECONDS", 0.0):
                with self.assertRaises(TimeoutError):
                    _wait_for_volume_muted(cast, True)

class CastMediaTest(unittest.TestCase):
    def test_reset_closes_current_connection(self):
        browser = SimpleNamespace(actions=[])

        def stop_discovery():
            browser.actions.append(("stop_discovery",))

        browser.stop_discovery = stop_discovery
        client = PyChromecastClient(
            CastConfig(name="Example", url="http://example.local/white-noise.mp4")
        )
        client._cast = SimpleNamespace()
        client._browser = browser

        client.reset()

        self.assertIsNone(client._cast)
        self.assertIsNone(client._browser)
        self.assertEqual(browser.actions, [("stop_discovery",)])

    def test_pause_uses_media_controller_pause(self):
        media = FakeMediaController("http://example.local/white-noise.mp4")
        client = PyChromecastClient(
            CastConfig(name="Example", url="http://example.local/white-noise.mp4")
        )
        client._cast = SimpleNamespace(media_controller=media)

        client.pause()

        self.assertEqual(media.actions, [("pause",)])

    def test_seek_to_start_uses_media_controller_seek_zero(self):
        media = FakeMediaController("http://example.local/white-noise.mp4")
        client = PyChromecastClient(
            CastConfig(name="Example", url="http://example.local/white-noise.mp4")
        )
        client._cast = SimpleNamespace(media_controller=media)

        client.seek_to_start()

        self.assertEqual(media.actions, [("seek", 0)])

    def test_load_waits_until_expected_media_is_reported(self):
        url = "http://example.local/white-noise.mp4"
        media = FakeMediaController(url)
        client = PyChromecastClient(CastConfig(name="Example", url=url))
        client._cast = SimpleNamespace(media_controller=media)

        client.load(autoplay=False)

        self.assertEqual(
            media.actions,
            [
                ("play_media", url, "video/mp4", False, "BUFFERED"),
                ("block_until_active", 5),
                ("update_status",),
            ],
        )
        self.assertEqual(media.status.content_id, url)

    def test_wait_for_media_loaded_refreshes_media_status_until_matched(self):
        media = fake_media(content_id=None)

        def update_status(callback_function=None):
            media.status.content_id = "http://example.local/white-noise.mp4"
            if callback_function is not None:
                callback_function(True, {})

        media.update_status = update_status

        _wait_for_media_loaded(media, "http://example.local/white-noise.mp4")

        self.assertEqual(
            media.status.content_id,
            "http://example.local/white-noise.mp4",
        )

    def test_wait_for_media_loaded_times_out_when_content_never_matches(self):
        media = fake_media(content_id="http://example.local/other.mp4")

        with patch("white_noise_keeper.cast.MEDIA_LOAD_CONFIRM_TIMEOUT_SECONDS", 0.01):
            with patch("white_noise_keeper.cast.MEDIA_LOAD_CONFIRM_INTERVAL_SECONDS", 0.0):
                with self.assertRaises(TimeoutError):
                    _wait_for_media_loaded(
                        media,
                        "http://example.local/white-noise.mp4",
                    )

    def test_refresh_media_status_times_out_when_cast_does_not_answer(self):
        media = fake_media(content_id="http://example.local/white-noise.mp4")
        media.update_status = lambda callback_function=None: None

        with patch(
            "white_noise_keeper.cast.MEDIA_STATUS_REFRESH_TIMEOUT_SECONDS",
            0.01,
        ):
            with self.assertRaises(TimeoutError):
                _refresh_media_status(media)


def fake_cast(volume_level=0.9, volume_muted=False):
    receiver_controller = SimpleNamespace(update_status=lambda callback_function=None: None)

    cast = SimpleNamespace(
        actions=[],
        status=SimpleNamespace(volume_level=volume_level, volume_muted=volume_muted),
        socket_client=SimpleNamespace(receiver_controller=receiver_controller),
    )

    def set_volume_muted(muted):
        cast.actions.append(("set_volume_muted", muted))

    cast.set_volume_muted = set_volume_muted
    return cast


def fake_media(content_id):
    def update_status(callback_function=None):
        if callback_function is not None:
            callback_function(True, {})

    return SimpleNamespace(
        status=SimpleNamespace(content_id=content_id),
        target_platform=True,
        _socket_client=SimpleNamespace(),
        update_status=update_status,
    )


class FakeMediaController:
    def __init__(self, expected_url):
        self.expected_url = expected_url
        self.status = SimpleNamespace(content_id=None)
        self.target_platform = True
        self._socket_client = SimpleNamespace()
        self.actions = []

    def play_media(
        self,
        url,
        content_type,
        *,
        autoplay,
        stream_type,
        callback_function=None,
    ):
        self.actions.append(("play_media", url, content_type, autoplay, stream_type))
        if callback_function is not None:
            callback_function(True, {})

    def block_until_active(self, timeout):
        self.actions.append(("block_until_active", timeout))

    def pause(self):
        self.actions.append(("pause",))

    def seek(self, position):
        self.actions.append(("seek", position))

    def update_status(self, callback_function=None):
        self.actions.append(("update_status",))
        self.status.content_id = self.expected_url
        if callback_function is not None:
            callback_function(True, {})


if __name__ == "__main__":
    unittest.main()
