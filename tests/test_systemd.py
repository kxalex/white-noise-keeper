import os
import unittest

from white_noise_keeper.systemd import SystemdNotifier


class SystemdNotifierTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("WATCHDOG_USEC", None)
        os.environ.pop("NOTIFY_SOCKET", None)

    def test_watchdog_interval_seconds_uses_half_of_watchdog_usec(self):
        os.environ["NOTIFY_SOCKET"] = "/tmp/test-notify.socket"
        os.environ["WATCHDOG_USEC"] = "30000000"
        notifier = SystemdNotifier()

        self.assertEqual(notifier.watchdog_interval_seconds(), 15.0)

    def test_watchdog_interval_seconds_returns_none_without_watchdog_env(self):
        notifier = SystemdNotifier()

        self.assertIsNone(notifier.watchdog_interval_seconds())


if __name__ == "__main__":
    unittest.main()
