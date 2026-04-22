import unittest

import datetime

from white_noise_keeper.cast import CastState, PLAYER_PAUSED, PLAYER_PLAYING
from white_noise_keeper.config import AppConfig, CastConfig, HttpConfig, MonitorConfig
from white_noise_keeper.keeper import WhiteNoiseKeeper, _retry_sleep_seconds
from white_noise_keeper.state import RuntimeState


EXPECTED_URL = "http://example.local/white-noise.mp4"
EXPECTED_CAST_NAME = "Example Cast"


class InMemoryStateStore:
    def __init__(self, state=None):
        self.state = state or RuntimeState()
        self.saved = 0

    def load(self):
        return self.state

    def save(self, state):
        self.state = state
        self.saved += 1


class FakeCast:
    def __init__(
        self,
        state=None,
        fail_get_state_times=0,
        fail_set_muted_to=None,
    ):
        self.state = state or cast_state(
            content_id=EXPECTED_URL,
            player_state=PLAYER_PLAYING,
        )
        self.fail_get_state_times = fail_get_state_times
        self.fail_set_muted_to = fail_set_muted_to
        self.actions = []

    def get_state(self):
        if self.fail_get_state_times > 0:
            self.fail_get_state_times -= 1
            raise RuntimeError("cast unavailable")
        return self.state

    def load(self, autoplay):
        self.actions.append(("load", autoplay))
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

    def reset(self):
        self.actions.append(("reset",))


class KeeperTest(unittest.TestCase):
    def test_retry_sleep_seconds_backs_off_up_to_thirty_seconds(self):
        self.assertEqual(_retry_sleep_seconds(2.0, 0), 2.0)
        self.assertEqual(_retry_sleep_seconds(2.0, 1), 2.0)
        self.assertEqual(_retry_sleep_seconds(2.0, 2), 4.0)
        self.assertEqual(_retry_sleep_seconds(2.0, 3), 8.0)
        self.assertEqual(_retry_sleep_seconds(2.0, 5), 10.0)

    def test_run_once_persists_manual_tap_as_new_snapshot(self):
        cast = FakeCast(
            cast_state(
                content_id="http://example.local/manual.mp4",
                player_state=PLAYER_PLAYING,
                volume_muted=True,
            )
        )
        state_store = InMemoryStateStore(
            RuntimeState(
                last_cast_state=snapshot(
                    content_id=EXPECTED_URL,
                    player_state=PLAYER_PAUSED,
                    volume_muted=False,
                )
            )
        )
        keeper = build_keeper(cast=cast, state_store=state_store)

        result = keeper.run_once()

        self.assertTrue(result.healthy)
        self.assertEqual(keeper.state.last_cast_state, snapshot_from_cast_state(cast.state))
        self.assertEqual(state_store.saved, 1)

    def test_run_once_restores_last_successful_state_after_failure(self):
        state = cast_state(
            content_id="http://example.local/other.mp4",
            player_state=PLAYER_PAUSED,
            volume_muted=False,
        )
        cast = FakeCast(state=state, fail_get_state_times=1)
        state_store = InMemoryStateStore(
            RuntimeState(
                last_cast_state=snapshot(
                    content_id=EXPECTED_URL,
                    player_state=PLAYER_PLAYING,
                    volume_muted=True,
                )
            )
        )
        keeper = build_keeper(cast=cast, state_store=state_store)

        result = keeper.run_once()

        self.assertTrue(result.healthy)
        self.assertEqual(
            cast.actions,
            [
                ("set_muted", True),
                ("load", True),
                ("set_muted", True),
            ],
        )
        self.assertEqual(keeper.state.last_cast_state, snapshot_from_cast_state(cast.state))

    def test_run_once_restores_last_media_snapshot_when_cast_reports_idle(self):
        cast = FakeCast(
            cast_state(
                content_id=None,
                player_state=None,
                volume_muted=False,
            )
        )
        state_store = InMemoryStateStore(
            RuntimeState(
                last_cast_state=snapshot(
                    content_id=EXPECTED_URL,
                    player_state=PLAYER_PLAYING,
                    volume_muted=True,
                )
            )
        )
        keeper = build_keeper(cast=cast, state_store=state_store)

        result = keeper.run_once()

        self.assertTrue(result.healthy)
        self.assertEqual(
            cast.actions,
            [
                ("set_muted", True),
                ("load", True),
                ("set_muted", True),
            ],
        )
        self.assertEqual(keeper.state.last_cast_state, snapshot_from_cast_state(cast.state))

    def test_run_once_does_not_save_idle_as_last_good_state(self):
        cast = FakeCast(
            cast_state(
                content_id=None,
                player_state=None,
                volume_muted=False,
            )
        )
        state_store = InMemoryStateStore()
        keeper = build_keeper(cast=cast, state_store=state_store)

        result = keeper.run_once()

        self.assertTrue(result.healthy)
        self.assertIsNone(keeper.state.last_cast_state)

    def test_start_records_exact_state_and_command_name(self):
        cast = FakeCast(
            cast_state(
                content_id=EXPECTED_URL,
                player_state=PLAYER_PAUSED,
                volume_muted=True,
            )
        )
        keeper = build_keeper(cast=cast)

        snapshot = keeper.command_start()

        self.assertEqual(snapshot["last_command"]["action"], "start")
        self.assertEqual(keeper.state.last_command["action"], "start")
        self.assertEqual(cast.actions, [("play",), ("set_muted", False)])
        self.assertFalse(keeper.state.last_cast_state["volume_muted"])

    def test_stop_records_paused_state_exactly(self):
        cast = FakeCast(
            cast_state(
                content_id=EXPECTED_URL,
                player_state=PLAYER_PLAYING,
                volume_muted=True,
            )
        )
        keeper = build_keeper(cast=cast)

        snapshot = keeper.command_stop()

        self.assertEqual(snapshot["last_command"]["action"], "stop")
        self.assertEqual(
            cast.actions,
            [
                ("seek_to_start",),
                ("pause",),
            ],
        )
        self.assertTrue(keeper.state.last_cast_state["volume_muted"])
        self.assertEqual(keeper.state.last_cast_state["player_state"], PLAYER_PAUSED)

    def test_start_unmutes_before_returning_state(self):
        cast = FakeCast(
            cast_state(
                content_id=EXPECTED_URL,
                player_state=PLAYER_PAUSED,
                volume_muted=True,
            )
        )
        keeper = build_keeper(cast=cast)

        snapshot = keeper.command_start()

        self.assertEqual(snapshot["last_command"]["action"], "start")
        self.assertEqual(
            cast.actions,
            [
                ("play",),
                ("set_muted", False),
            ],
        )
        self.assertFalse(keeper.state.last_cast_state["volume_muted"])

    def test_run_once_starts_at_8pm(self):
        cast = FakeCast(
            cast_state(
                content_id=EXPECTED_URL,
                player_state=PLAYER_PAUSED,
                volume_muted=True,
            )
        )
        times = [
            datetime.datetime(2026, 4, 22, 19, 59).timestamp(),
            datetime.datetime(2026, 4, 22, 20, 0).timestamp(),
        ]
        keeper = build_keeper(cast=cast, clock=lambda: times.pop(0))

        first = keeper.run_once()
        second = keeper.run_once()

        self.assertTrue(first.healthy)
        self.assertTrue(second.healthy)
        self.assertEqual(
            cast.actions,
            [
                ("play",),
                ("set_muted", False),
            ],
        )

def build_keeper(cast, state_store=None, clock=None):
    config = AppConfig(
        cast=CastConfig(name=EXPECTED_CAST_NAME, url=EXPECTED_URL),
        monitor=MonitorConfig(interval_seconds=5),
        http=HttpConfig(enabled=False),
    )
    keeper = WhiteNoiseKeeper(
        config=config,
        cast_client=cast,
        state_store=state_store or InMemoryStateStore(),
        clock=clock or (lambda: 100.0),
    )
    keeper.playback.audio_load_guard.sleep = lambda seconds: None
    return keeper


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


def snapshot(
    content_id=EXPECTED_URL,
    player_state=PLAYER_PLAYING,
    current_time=0,
    duration=3600,
    volume_muted=False,
    volume_level=0.77,
):
    return {
        "content_id": content_id,
        "player_state": player_state,
        "current_time": current_time,
        "duration": duration,
        "volume_muted": volume_muted,
        "volume_level": volume_level,
    }


def snapshot_from_cast_state(state):
    return snapshot(
        content_id=state.content_id,
        player_state=state.player_state,
        current_time=state.current_time,
        duration=state.duration,
        volume_muted=state.volume_muted,
        volume_level=state.volume_level,
    )


if __name__ == "__main__":
    unittest.main()
