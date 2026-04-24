import datetime
import unittest

from white_noise_keeper.stats import (
    current_bucket_bounds,
    normalize_stats,
    snapshot_stats,
)


class StatsTest(unittest.TestCase):
    def test_bucket_starts_at_local_noon(self):
        before_noon = datetime.datetime(2026, 4, 24, 11, 59, 59).timestamp()
        at_noon = datetime.datetime(2026, 4, 24, 12, 0, 0).timestamp()

        before_start, before_end = current_bucket_bounds(before_noon)
        noon_start, noon_end = current_bucket_bounds(at_noon)

        self.assertEqual(
            before_start,
            datetime.datetime(2026, 4, 23, 12, 0, 0).timestamp(),
        )
        self.assertEqual(
            before_end,
            datetime.datetime(2026, 4, 24, 12, 0, 0).timestamp(),
        )
        self.assertEqual(
            noon_start,
            datetime.datetime(2026, 4, 24, 12, 0, 0).timestamp(),
        )
        self.assertEqual(
            noon_end,
            datetime.datetime(2026, 4, 25, 12, 0, 0).timestamp(),
        )

    def test_prunes_failure_records_older_than_last_week(self):
        now = datetime.datetime(2026, 4, 24, 12, 30, 0).timestamp()
        recent = now - (2 * 24 * 60 * 60)
        stale = now - (8 * 24 * 60 * 60)

        stats = normalize_stats(
            {
                "open_outage": {"started_at": recent, "reason": "nest_unavailable"},
                "failure_records": [
                    {
                        "started_at": stale - 20,
                        "ended_at": stale - 10,
                        "reason": "nest_unavailable",
                        "duration_seconds": 10.0,
                    },
                    {
                        "started_at": recent - 20,
                        "ended_at": recent - 10,
                        "reason": "nest_unavailable",
                        "duration_seconds": 10.0,
                    },
                ],
            },
            now,
        )

        self.assertEqual(len(stats["failure_records"]), 1)
        self.assertAlmostEqual(stats["failure_records"][0]["ended_at"], recent - 10)
        self.assertEqual(stats["open_outage"]["started_at"], recent)

    def test_daily_summary_counts_overlap_with_open_outage(self):
        now = datetime.datetime(2026, 4, 24, 12, 30, 0).timestamp()
        stats = {
            "open_outage": {
                "started_at": datetime.datetime(2026, 4, 24, 11, 55, 0).timestamp(),
                "reason": "nest_unavailable",
            },
            "failure_records": [
                {
                    "started_at": datetime.datetime(2026, 4, 24, 11, 10, 0).timestamp(),
                    "ended_at": datetime.datetime(2026, 4, 24, 11, 20, 0).timestamp(),
                    "reason": "nest_unavailable",
                    "duration_seconds": 600.0,
                },
                {
                    "started_at": datetime.datetime(2026, 4, 24, 12, 10, 0).timestamp(),
                    "ended_at": datetime.datetime(2026, 4, 24, 12, 20, 0).timestamp(),
                    "reason": "nest_unavailable",
                    "duration_seconds": 600.0,
                },
            ],
        }

        snapshot = snapshot_stats(stats, now)

        self.assertEqual(snapshot["daily"]["count"], 2)
        self.assertEqual(snapshot["daily"]["bucket_start"], datetime.datetime(2026, 4, 24, 12, 0, 0).timestamp())
        self.assertEqual(snapshot["daily"]["bucket_end"], datetime.datetime(2026, 4, 25, 12, 0, 0).timestamp())
        self.assertEqual(snapshot["daily"]["total_seconds"], 2400.0)
        self.assertEqual(len(snapshot["failure_records"]), 2)
        self.assertEqual(snapshot["open_outage"]["reason"], "nest_unavailable")


if __name__ == "__main__":
    unittest.main()
