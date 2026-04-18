import unittest
from datetime import time

from white_noise_keeper.time_window import in_active_window, parse_hhmm


class TimeWindowTest(unittest.TestCase):
    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("20:05"), time(20, 5))

    def test_active_window_without_midnight_wrap(self):
        self.assertTrue(in_active_window(time(10, 0), time(9, 0), time(17, 0)))
        self.assertFalse(in_active_window(time(8, 59), time(9, 0), time(17, 0)))
        self.assertFalse(in_active_window(time(17, 0), time(9, 0), time(17, 0)))

    def test_active_window_with_midnight_wrap(self):
        self.assertTrue(in_active_window(time(20, 0), time(20, 0), time(8, 0)))
        self.assertTrue(in_active_window(time(2, 30), time(20, 0), time(8, 0)))
        self.assertFalse(in_active_window(time(8, 0), time(20, 0), time(8, 0)))
        self.assertFalse(in_active_window(time(12, 0), time(20, 0), time(8, 0)))

    def test_equal_start_and_end_means_always_active(self):
        self.assertTrue(in_active_window(time(12, 0), time(8, 0), time(8, 0)))


if __name__ == "__main__":
    unittest.main()
