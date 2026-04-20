import unittest
from datetime import datetime

from white_noise_keeper.cast import CastState, PLAYER_PAUSED, PLAYER_PLAYING
from white_noise_keeper.config import (
    AppConfig,
    CastConfig,
    IpadBackupConfig,
    MonitorConfig,
    ScheduleConfig,
)
from white_noise_keeper.keeper import WhiteNoiseKeeper
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
        fail=False,
    ):
        self.state = state or cast_state(
            content_id=EXPECTED_URL,
            player_state=PLAYER_PLAYING,
        )
        self.fail = fail
        self.actions = []

    def get_state(self):
        if self.fail:
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
        self.state = cast_state(
            content_id=self.state.content_id,
            player_state=self.state.player_state,
            volume_muted=muted,
            volume_level=self.state.volume_level,
        )

    def close(self):
        self.actions.append(("close",))


class FakePushcut:
    def __init__(self):
        self.play_calls = 0
        self.stop_calls = 0

    def trigger_play(self, dry_run=False):
        self.play_calls += 1

    def trigger_stop(self, dry_run=False):
        self.stop_calls += 1


class KeeperTest(unittest.TestCase):
    def test_active_window_enforces_playback_when_expected_media_is_paused(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED))
        keeper = build_keeper(cast=cast)

        result = keeper.run_once(active_datetime())

        self.assertTrue(result.healthy)
        self.assertIn(("play",), cast.actions)

    def test_outside_window_does_not_enforce_playback_when_expected_media_is_paused(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED))
        keeper = build_keeper(cast=cast)

        result = keeper.run_once(outside_datetime())

        self.assertTrue(result.healthy)
        self.assertEqual(cast.actions, [])

    def test_ipad_backup_triggers_after_failure_threshold_and_not_again_during_cooldown(self):
        cast = FakeCast(fail=True)
        pushcut = FakePushcut()
        clock = SequenceClock([100.0, 131.0, 140.0])
        keeper = build_keeper(cast=cast, pushcut=pushcut, clock=clock)

        self.assertFalse(keeper.run_once(active_datetime()).healthy)
        self.assertEqual(pushcut.play_calls, 0)

        self.assertFalse(keeper.run_once(active_datetime()).healthy)
        self.assertEqual(pushcut.play_calls, 1)
        self.assertTrue(keeper.state.ipad_backup_active)

        self.assertFalse(keeper.run_once(active_datetime()).healthy)
        self.assertEqual(pushcut.play_calls, 1)

    def test_ipad_backup_stops_after_ten_stable_minutes(self):
        cast = FakeCast()
        pushcut = FakePushcut()
        state_store = InMemoryStateStore(RuntimeState(ipad_backup_active=True))
        clock = SequenceClock([100.0, 699.0, 700.0])
        keeper = build_keeper(
            cast=cast,
            pushcut=pushcut,
            state_store=state_store,
            clock=clock,
        )

        self.assertTrue(keeper.run_once(active_datetime()).healthy)
        self.assertEqual(pushcut.stop_calls, 0)

        self.assertTrue(keeper.run_once(active_datetime()).healthy)
        self.assertEqual(pushcut.stop_calls, 0)

        self.assertTrue(keeper.run_once(active_datetime()).healthy)
        self.assertEqual(pushcut.stop_calls, 1)
        self.assertFalse(keeper.state.ipad_backup_active)

    def test_ipad_stop_timer_cancels_when_nest_fails_again(self):
        cast = FakeCast()
        pushcut = FakePushcut()
        state_store = InMemoryStateStore(RuntimeState(ipad_backup_active=True))
        clock = SequenceClock([100.0, 200.0, 700.0])
        keeper = build_keeper(
            cast=cast,
            pushcut=pushcut,
            state_store=state_store,
            clock=clock,
        )

        self.assertTrue(keeper.run_once(active_datetime()).healthy)
        cast.fail = True
        self.assertFalse(keeper.run_once(active_datetime()).healthy)
        cast.fail = False
        self.assertTrue(keeper.run_once(active_datetime()).healthy)

        self.assertEqual(pushcut.stop_calls, 0)
        self.assertEqual(keeper.state.nest_recovered_started_at, 700.0)

    def test_ipad_backup_stops_immediately_when_active_window_ends(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED))
        pushcut = FakePushcut()
        state_store = InMemoryStateStore(RuntimeState(ipad_backup_active=True))
        keeper = build_keeper(cast=cast, pushcut=pushcut, state_store=state_store)

        result = keeper.run_once(outside_datetime())

        self.assertTrue(result.healthy)
        self.assertEqual(pushcut.stop_calls, 1)
        self.assertFalse(keeper.state.ipad_backup_active)

    def test_disabled_ipad_backup_never_triggers_pushcut(self):
        cast = FakeCast(fail=True)
        pushcut = FakePushcut()
        config = AppConfig(
            cast=CastConfig(name=EXPECTED_CAST_NAME, url=EXPECTED_URL),
            schedule=ScheduleConfig(active_start="20:00", active_end="08:00"),
            monitor=MonitorConfig(interval_seconds=5),
            ipad_backup=IpadBackupConfig(enabled=False),
        )
        keeper = WhiteNoiseKeeper(
            config=config,
            cast_client=cast,
            state_store=InMemoryStateStore(),
            pushcut_client=pushcut,
            clock=SequenceClock([100.0, 1000.0]),
        )

        self.assertFalse(keeper.run_once(active_datetime()).healthy)
        self.assertFalse(keeper.run_once(active_datetime()).healthy)
        self.assertEqual(pushcut.play_calls, 0)

    def test_force_start_expires_after_until_timestamp(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED))
        keeper = build_keeper(cast=cast, clock=SequenceClock([1001.0]))
        keeper.state.manual_mode = "force"
        keeper.state.manual_until = 1000.0

        result = keeper.run_once(outside_datetime())

        self.assertTrue(result.healthy)
        self.assertIsNone(keeper.state.manual_mode)
        self.assertIsNone(keeper.state.manual_until)
        self.assertNotIn(("play",), cast.actions)

    def test_stop_suppresses_until_next_active_window_and_is_cleared_by_expiry(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED))
        keeper = build_keeper(cast=cast, clock=SequenceClock([100.0, 201.0]))
        keeper.state.manual_mode = "suppress"
        keeper.state.manual_until = 200.0

        first = keeper.run_once(active_datetime())
        self.assertTrue(first.healthy)
        self.assertEqual(cast.actions, [])
        self.assertEqual(first.message, "Nest auto-start is suppressed")

        second = keeper.run_once(active_datetime())
        self.assertTrue(second.healthy)
        self.assertIn(("play",), cast.actions)
        self.assertIsNone(keeper.state.manual_mode)
        self.assertIsNone(keeper.state.manual_until)

    def test_stop_sets_suppression_until_next_active_window(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PLAYING))
        keeper = build_keeper(cast=cast, clock=SequenceClock([100.0] * 10))
        keeper.state.manual_mode = "force"
        keeper.state.manual_until = 999999.0

        snapshot = keeper.command_stop()

        self.assertEqual(keeper.state.manual_mode, "suppress")
        self.assertIsNotNone(keeper.state.manual_until)
        self.assertEqual(
            cast.actions,
            [
                ("seek_to_start",),
                ("pause",),
            ],
        )
        self.assertTrue(snapshot["suppressed"])
        self.assertEqual(snapshot["manual_mode"], "suppress")
        self.assertTrue(snapshot["manual_until"] is not None)

    def test_start_clears_suppression_plays_once_then_respects_manual_pause(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED))
        keeper = build_keeper(cast=cast)
        keeper.state.manual_mode = "suppress"
        keeper.state.manual_until = 999999.0

        snapshot = keeper.command_start()

        self.assertIsNone(keeper.state.manual_mode)
        self.assertIsNone(keeper.state.manual_until)
        self.assertEqual(snapshot["last_command"]["action"], "start")
        self.assertIn(("play",), cast.actions)

        cast.actions.clear()
        cast.state = cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED)
        result = keeper.run_once(outside_datetime())

        self.assertTrue(result.healthy)
        self.assertEqual(cast.actions, [])

    def test_start_force_replays_after_manual_pause_until_active_window_end(self):
        cast = FakeCast(cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED))
        keeper = build_keeper(cast=cast, clock=SequenceClock([100.0] * 10))

        keeper.command_start_force()
        cast.actions.clear()
        cast.state = cast_state(content_id=EXPECTED_URL, player_state=PLAYER_PAUSED)
        result = keeper.run_once(outside_datetime())

        self.assertTrue(result.healthy)
        self.assertIn(("play",), cast.actions)


def build_keeper(cast, pushcut=None, state_store=None, clock=None):
    config = AppConfig(
        cast=CastConfig(name=EXPECTED_CAST_NAME, url=EXPECTED_URL),
        schedule=ScheduleConfig(active_start="20:00", active_end="08:00"),
        monitor=MonitorConfig(interval_seconds=5),
        ipad_backup=IpadBackupConfig(
            enabled=True,
            play_url="https://pushcut.example/play",
            stop_url="https://pushcut.example/stop",
            trigger_after_failure_seconds=30,
            retrigger_cooldown_seconds=1800,
            stop_after_recovered_seconds=600,
        ),
    )
    return WhiteNoiseKeeper(
        config=config,
        cast_client=cast,
        state_store=state_store or InMemoryStateStore(),
        pushcut_client=pushcut or FakePushcut(),
        clock=clock or SequenceClock([100.0] * 20),
        sleep=lambda _seconds: None,
    )


def cast_state(
    content_id=EXPECTED_URL,
    player_state=PLAYER_PLAYING,
    volume_muted=False,
    volume_level=0.77,
):
    return CastState(
        content_id=content_id,
        player_state=player_state,
        current_time=0,
        duration=3600,
        volume_muted=volume_muted,
        volume_level=volume_level,
    )


def active_datetime():
    return datetime(2026, 1, 1, 21, 0, 0)


def outside_datetime():
    return datetime(2026, 1, 1, 12, 0, 0)


class SequenceClock:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        if len(self.values) == 1:
            return self.values[0]
        return self.values.pop(0)


if __name__ == "__main__":
    unittest.main()
