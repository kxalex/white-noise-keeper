import unittest

from white_noise_keeper.cast import CastState, PLAYER_PAUSED, PLAYER_PLAYING
from white_noise_keeper.playback import WhiteNoisePlayback


EXPECTED_URL = "http://example.local/white-noise.mp4"


class FakeCast:
    def __init__(
        self,
        state=None,
        fail_load=False,
        fail_restore_volume=False,
        fail_restore_mute=False,
    ):
        self.state = state or cast_state(
            content_id=EXPECTED_URL,
            player_state=PLAYER_PLAYING,
        )
        self.fail_load = fail_load
        self.fail_restore_volume = fail_restore_volume
        self.fail_restore_mute = fail_restore_mute
        self.actions = []

    def get_state(self):
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

    def set_muted(self, muted):
        self.actions.append(("set_muted", muted))
        if self.fail_restore_mute and muted is False:
            raise RuntimeError("mute restore failed")
        self.state = cast_state(
            content_id=self.state.content_id,
            player_state=self.state.player_state,
            volume_muted=muted,
            volume_level=self.state.volume_level,
        )

    def set_volume_level(self, level):
        self.actions.append(("set_volume_level", level))
        if self.fail_restore_volume:
            raise RuntimeError("volume restore failed")
        self.state = cast_state(
            content_id=self.state.content_id,
            player_state=self.state.player_state,
            volume_muted=self.state.volume_muted,
            volume_level=level,
        )

    def close(self):
        self.actions.append(("close",))


class PlaybackTest(unittest.TestCase):
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

        self.assertEqual(cast.actions, muted_load_actions(autoplay=True, volume=None))
        self.assertIsNone(cast.state.volume_level)

    def test_load_restores_zero_volume_as_audible_default(self):
        cast = FakeCast(
            cast_state(
                content_id="http://example.local/other.mp4",
                volume_level=0.0,
            )
        )
        playback = build_playback(cast)

        playback.ensure_loaded(autoplay=False)

        self.assertEqual(cast.actions, muted_load_actions(autoplay=False, volume=0.80))
        self.assertEqual(cast.state.volume_level, 0.80)

    def test_load_failure_attempts_audio_restore_and_reraises_original_exception(self):
        cast = FakeCast(
            cast_state(content_id="http://example.local/other.mp4"),
            fail_load=True,
        )
        playback = build_playback(cast)

        with self.assertRaisesRegex(RuntimeError, "load failed"):
            playback.load_from_beginning_paused()

        self.assertEqual(cast.actions, muted_load_actions(autoplay=False))

    def test_restore_volume_failure_still_attempts_unmute_and_reraises(self):
        cast = FakeCast(
            cast_state(
                content_id="http://example.local/other.mp4",
                volume_level=0.0,
            ),
            fail_restore_volume=True,
        )
        playback = build_playback(cast)

        with self.assertRaisesRegex(RuntimeError, "volume restore failed"):
            playback.load_from_beginning_paused()

        self.assertEqual(
            cast.actions,
            [
                ("set_muted", True),
                ("load", False),
                ("set_volume_level", 0.80),
                ("set_volume_level", 0.80),
                ("set_muted", False),
            ],
        )

    def test_failed_audio_restore_is_retried_before_next_load(self):
        cast = FakeCast(
            cast_state(content_id="http://example.local/other.mp4"),
            fail_load=True,
            fail_restore_mute=True,
        )
        playback = build_playback(cast)

        with self.assertRaisesRegex(RuntimeError, "load failed"):
            playback.load_from_beginning_paused()

        self.assertTrue(cast.state.volume_muted)

        cast.fail_load = False
        cast.fail_restore_mute = False
        cast.actions.clear()
        playback.ensure_loaded(autoplay=False)

        self.assertEqual(
            cast.actions,
            [
                ("set_muted", False),
                ("set_muted", True),
                ("load", False),
                ("set_muted", False),
            ],
        )
        self.assertFalse(cast.state.volume_muted)

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
    return WhiteNoisePlayback(cast, EXPECTED_URL)


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


def muted_load_actions(autoplay, volume=None):
    actions = [("set_muted", True), ("load", autoplay)]
    if volume is not None:
        actions.append(("set_volume_level", volume))
    actions.append(("set_muted", False))
    return actions


if __name__ == "__main__":
    unittest.main()
