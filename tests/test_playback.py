import unittest

from white_noise_keeper.cast import CastState, PLAYER_PAUSED, PLAYER_PLAYING
from white_noise_keeper.playback import (
    MUTE_AFTER_LOAD_DELAY_SECONDS,
    WhiteNoisePlayback,
    _format_current_media,
)


EXPECTED_URL = "http://example.local/white-noise.mp4"


class FakeCast:
    def __init__(
        self,
        state=None,
        fail_get_state_times=0,
        fail_load=False,
        fail_set_muted_to=None,
    ):
        self.state = state or cast_state(
            content_id=EXPECTED_URL,
            player_state=PLAYER_PLAYING,
        )
        self.fail_get_state_times = fail_get_state_times
        self.fail_load = fail_load
        self.fail_set_muted_to = fail_set_muted_to
        self.actions = []

    def get_state(self):
        if self.fail_get_state_times > 0:
            self.fail_get_state_times -= 1
            raise RuntimeError("cast unavailable")
        return self.state

    def load(self, autoplay):
        self.actions.append(("load", autoplay))
        if self.fail_load:
            raise RuntimeError("load failed")
        self.state = cast_state(
            content_id=EXPECTED_URL,
            player_state=PLAYER_PLAYING if autoplay else PLAYER_PAUSED,
            volume_muted=self.state.volume_muted,
            volume_level=self.state.volume_level,
        )

    def play(self):
        self.actions.append(("play",))
        self.state = cast_state(
            content_id=self.state.content_id,
            player_state=PLAYER_PLAYING,
            volume_muted=self.state.volume_muted,
            volume_level=self.state.volume_level,
        )

    def pause(self):
        self.actions.append(("pause",))
        self.state = cast_state(
            content_id=self.state.content_id,
            player_state=PLAYER_PAUSED,
            volume_muted=self.state.volume_muted,
            volume_level=self.state.volume_level,
        )

    def seek_to_start(self):
        self.actions.append(("seek_to_start",))
        self.state = cast_state(
            content_id=self.state.content_id,
            player_state=self.state.player_state,
            volume_muted=self.state.volume_muted,
            volume_level=self.state.volume_level,
        )

    def set_muted(self, muted):
        self.actions.append(("set_muted", muted))
        if self.fail_set_muted_to is not None and muted == self.fail_set_muted_to:
            raise RuntimeError("mute restore failed")
        self.state = cast_state(
            content_id=self.state.content_id,
            player_state=self.state.player_state,
            volume_muted=muted,
            volume_level=self.state.volume_level,
        )

    def close(self):
        self.actions.append(("close",))


class PlaybackTest(unittest.TestCase):
    def test_format_current_media_returns_idle_when_nothing_is_loaded(self):
        state = cast_state(content_id=None, player_state=None)

        self.assertEqual(_format_current_media(state), "Idle")

    def test_format_current_media_includes_loaded_media_and_state(self):
        state = cast_state(
            content_id="http://example.local/other.mp4",
            player_state=PLAYER_PAUSED,
        )

        self.assertEqual(
            _format_current_media(state),
            "http://example.local/other.mp4 (PAUSED)",
        )

    def test_ensure_playing_plays_when_expected_media_is_paused(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED))
        playback = build_playback(cast)

        state = playback.ensure_playing()

        self.assertTrue(state.playing)
        self.assertEqual(cast.actions, [("play",)])

    def test_ensure_playing_loads_wrong_media_and_plays(self):
        cast = FakeCast(
            cast_state(
                content_id="http://example.local/other.mp4",
                player_state=PLAYER_PLAYING,
            )
        )
        playback = build_playback(cast)

        state = playback.ensure_playing()

        self.assertTrue(state.playing)
        self.assertEqual(cast.actions, muted_load_actions(autoplay=True))

    def test_ensure_loaded_leaves_expected_media_alone(self):
        for player_state in (PLAYER_PLAYING, PLAYER_PAUSED):
            with self.subTest(player_state=player_state):
                cast = FakeCast(
                    cast_state(content_id=EXPECTED_URL, player_state=player_state)
                )
                playback = build_playback(cast)

                state = playback.ensure_loaded(autoplay=False)

                self.assertEqual(state.player_state, player_state)
                self.assertEqual(cast.actions, [])

    def test_ensure_loaded_loads_wrong_media_paused(self):
        cast = FakeCast(cast_state(content_id="http://example.local/other.mp4"))
        playback = build_playback(cast)

        state = playback.ensure_loaded(autoplay=False)

        self.assertEqual(state.player_state, PLAYER_PAUSED)
        self.assertEqual(cast.actions, muted_load_actions(autoplay=False))

    def test_ensure_loaded_reloads_near_end_preserving_play_state(self):
        for player_state, autoplay in (
            (PLAYER_PLAYING, True),
            (PLAYER_PAUSED, False),
        ):
            with self.subTest(player_state=player_state):
                cast = FakeCast(
                    cast_state(
                        content_id=EXPECTED_URL,
                        player_state=player_state,
                        current_time=3541,
                        duration=3600,
                    )
                )
                playback = build_playback(cast)

                state = playback.ensure_loaded(autoplay=False)

                self.assertEqual(
                    state.player_state,
                    PLAYER_PLAYING if autoplay else PLAYER_PAUSED,
                )
                self.assertEqual(cast.actions, muted_load_actions(autoplay=autoplay))

    def test_near_end_reload_restores_mute_when_volume_level_is_unknown(self):
        cast = FakeCast(
            cast_state(current_time=3541, duration=3600, volume_level=None)
        )
        playback = build_playback(cast)

        playback.ensure_loaded(autoplay=False)

        self.assertEqual(cast.actions, muted_load_actions(autoplay=True))
        self.assertIsNone(cast.state.volume_level)

    def test_load_failure_attempts_audio_restore_and_reraises_original_exception(self):
        cast = FakeCast(
            cast_state(content_id="http://example.local/other.mp4"),
            fail_load=True,
        )
        playback = build_playback(cast)

        with self.assertRaisesRegex(RuntimeError, "load failed"):
            playback.ensure_loaded(autoplay=False)

        self.assertEqual(
            cast.actions,
            [("set_muted", True), ("load", False), ("set_muted", False)],
        )

    def test_failed_mute_restore_is_retried_before_next_load(self):
        cast = FakeCast(cast_state(content_id="http://example.local/other.mp4"), fail_set_muted_to=False)
        playback = build_playback(cast)

        with self.assertRaisesRegex(RuntimeError, "mute restore failed"):
            playback.ensure_loaded(autoplay=False)

        self.assertTrue(cast.state.volume_muted)

        cast.fail_set_muted_to = None
        cast.actions.clear()

        self.assertTrue(playback.ensure_loaded(autoplay=False))

        self.assertEqual(cast.actions, [("set_muted", False)])
        self.assertFalse(cast.state.volume_muted)

    def test_pause_at_beginning_seeks_then_pauses_loaded_media(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PLAYING))
        playback = build_playback(cast)

        playback.pause_at_beginning()

        self.assertEqual(cast.actions, [("seek_to_start",), ("pause",)])
        self.assertEqual(cast.state.player_state, PLAYER_PAUSED)

    def test_restore_snapshot_preserves_muted_state(self):
        cast = FakeCast(cast_state(content_id="http://example.local/other.mp4"))
        playback = build_playback(cast)

        restored = playback.restore_snapshot(
            {
                "content_id": EXPECTED_URL,
                "player_state": PLAYER_PLAYING,
                "volume_muted": True,
            }
        )

        self.assertEqual(
            cast.actions,
            [
                ("set_muted", True),
                ("load", True),
                ("sleep", MUTE_AFTER_LOAD_DELAY_SECONDS),
                ("set_muted", True),
            ],
        )
        self.assertTrue(restored.volume_muted)
        self.assertEqual(restored.content_id, EXPECTED_URL)

    def test_pause_at_beginning_does_nothing_when_no_media_loaded(self):
        cast = FakeCast(cast_state(content_id=None, player_state=None))
        playback = build_playback(cast)

        playback.pause_at_beginning()

        self.assertEqual(cast.actions, [])

    def test_expected_media_not_near_end_is_not_reloaded(self):
        cast = FakeCast(cast_state(current_time=3539, duration=3600))
        playback = build_playback(cast)

        playback.ensure_loaded(autoplay=False)

        self.assertEqual(cast.actions, [])

    def test_missing_media_timing_does_not_reload(self):
        for current_time, duration in ((None, 3600), (3541, None), (3541, 0)):
            with self.subTest(current_time=current_time, duration=duration):
                cast = FakeCast(
                    cast_state(current_time=current_time, duration=duration)
                )
                playback = build_playback(cast)

                playback.ensure_loaded(autoplay=False)

                self.assertEqual(cast.actions, [])


def build_playback(cast):
    playback = WhiteNoisePlayback(cast, EXPECTED_URL)
    playback.audio_load_guard.sleep = lambda seconds: cast.actions.append(
        ("sleep", seconds)
    )
    return playback


def cast_state(
    content_id=EXPECTED_URL,
    player_state=PLAYER_PLAYING,
    current_time=0,
    duration=3600,
    volume_muted=False,
    volume_level=0.77,
):
    return CastState(
        content_id=content_id,
        player_state=player_state,
        current_time=current_time,
        duration=duration,
        volume_muted=volume_muted,
        volume_level=volume_level,
    )


def muted_load_actions(autoplay, muted=False):
    return [
        ("set_muted", True),
        ("load", autoplay),
        ("sleep", MUTE_AFTER_LOAD_DELAY_SECONDS),
        ("set_muted", muted),
    ]


if __name__ == "__main__":
    unittest.main()
