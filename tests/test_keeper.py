import unittest

import datetime

from white_noise_keeper.cast import CastState, PLAYER_PAUSED, PLAYER_PLAYING
from white_noise_keeper.config import AppConfig, CastConfig, HttpConfig, MonitorConfig
from white_noise_keeper.keeper import (
    WhiteNoiseKeeper,
    _retry_sleep_seconds,
    _seconds_until_next_eight_pm,
)
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

    def test_run_once_replaces_different_media_with_expected_media(self):
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
        self.assertEqual(
            cast.actions,
            [
                ("set_muted", True),
                ("load", False),
                ("set_muted", False),
            ],
        )
        self.assertEqual(keeper.state.last_cast_state, snapshot_from_cast_state(cast.state))
        self.assertEqual(keeper.state.last_cast_state["content_id"], EXPECTED_URL)
        self.assertEqual(keeper.state.last_cast_state["player_state"], PLAYER_PAUSED)

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
                ("reset",),
                ("set_muted", True),
                ("load", True),
                ("set_muted", True),
            ],
        )
        self.assertEqual(keeper.state.last_cast_state, snapshot_from_cast_state(cast.state))

    def test_run_once_resets_cast_after_health_check_failure_without_state(self):
        cast = FakeCast(fail_get_state_times=1)
        keeper = build_keeper(cast=cast, state_store=InMemoryStateStore())

        result = keeper.run_once()

        self.assertFalse(result.healthy)
        self.assertEqual(result.message, "Nest unavailable; retrying")
        self.assertEqual(cast.actions, [("reset",)])

    def test_status_snapshot_returns_last_published_state(self):
        keeper = build_keeper(
            cast=FakeCast(),
            state_store=InMemoryStateStore(
                RuntimeState(
                    last_cast_state=snapshot(
                        content_id=EXPECTED_URL,
                        player_state=PLAYER_PAUSED,
                        volume_muted=False,
                    )
                )
            ),
        )
        keeper.state.last_cast_state = snapshot(
            content_id="http://example.local/in-flight.mp4",
            player_state=PLAYER_PLAYING,
            volume_muted=True,
        )

        status = keeper.status_snapshot()

        self.assertEqual(status["last_cast_state"]["content_id"], EXPECTED_URL)
        self.assertEqual(status["last_cast_state"]["player_state"], PLAYER_PAUSED)

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

    def test_run_once_loads_media_paused_when_no_state_exists(self):
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
        self.assertEqual(
            cast.actions,
            [
                ("set_muted", True),
                ("load", False),
                ("set_muted", False),
            ],
        )
        self.assertEqual(keeper.state.last_cast_state, snapshot_from_cast_state(cast.state))
        self.assertEqual(keeper.state.last_cast_state["player_state"], PLAYER_PAUSED)

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

    def test_seconds_until_next_eight_pm(self):
        just_before = datetime.datetime(2026, 4, 22, 19, 30).timestamp()
        just_after = datetime.datetime(2026, 4, 22, 20, 30).timestamp()

        self.assertEqual(_seconds_until_next_eight_pm(just_before), 1800.0)
        self.assertEqual(_seconds_until_next_eight_pm(just_after), 84600.0)

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
