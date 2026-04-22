import unittest

from white_noise_keeper.config import parse_config


class ConfigTest(unittest.TestCase):
    def test_cast_name_and_url_are_required(self):
        required_cases = [
            ({}, "cast section is required"),
            ({"cast": {"url": "http://example.local/noise.mp4"}}, "cast.name is required"),
            ({"cast": {"name": "Example Cast"}}, "cast.url is required"),
            (
                {"cast": {"name": " ", "url": "http://example.local/noise.mp4"}},
                "cast.name is required",
            ),
            (
                {"cast": {"name": "Example Cast", "url": ""}},
                "cast.url is required",
            ),
        ]

        for raw_config, message in required_cases:
            with self.subTest(raw_config=raw_config):
                with self.assertRaisesRegex(ValueError, message):
                    parse_config(raw_config)

    def test_valid_cast_config_loads_with_operational_defaults(self):
        config = parse_config(
            {
                "cast": {
                    "name": "Example Cast",
                    "url": "http://example.local/noise.mp4",
                }
            }
        )

        self.assertEqual(config.cast.name, "Example Cast")
        self.assertEqual(config.cast.url, "http://example.local/noise.mp4")
        self.assertEqual(config.cast.content_type, "video/mp4")
        self.assertEqual(config.monitor.interval_seconds, 2.0)


if __name__ == "__main__":
    unittest.main()
