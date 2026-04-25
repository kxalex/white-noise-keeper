import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from white_noise_keeper.api import start_api_server


class FakeKeeper:
    def __init__(self):
        self.calls = []

    def status_snapshot(self):
        self.calls.append("status")
        return {
            "ok": True,
            "last_cast_state": {"player_state": "PAUSED"},
        }

    def stats_snapshot(self):
        self.calls.append("stats")
        return {
            "ok": True,
            "daily": {
                "bucket_start": 100.0,
                "bucket_end": 200.0,
                "count": 2,
                "total_seconds": 30.0,
            },
            "open_outage": {"started_at": 150.0, "reason": "nest_unavailable"},
            "failure_records": [],
        }

    def command_start(self):
        self.calls.append("start")
        return {"ok": True, "last_command": {"action": "start"}}

    def command_stop(self):
        self.calls.append("stop")
        return {"ok": True, "last_command": {"action": "stop"}}


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.keeper = FakeKeeper()
        self.server = start_api_server(self.keeper, "127.0.0.1", 0)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_status_endpoint_returns_snapshot(self):
        response = self.get("/v1/status")

        self.assertTrue(response["ok"])
        self.assertEqual(response["last_cast_state"]["player_state"], "PAUSED")
        self.assertEqual(self.keeper.calls, ["status"])

    def test_stats_endpoint_returns_failure_history(self):
        response = self.get("/v1/stats", headers={"Accept": "application/json"})

        self.assertTrue(response["ok"])
        self.assertEqual(response["daily"]["count"], 2)
        self.assertEqual(response["daily"]["total_seconds"], 30.0)
        self.assertEqual(response["open_outage"]["reason"], "nest_unavailable")
        self.assertEqual(self.keeper.calls, ["stats"])

    def test_stats_endpoint_defaults_to_table(self):
        body, content_type = self.get_raw("/v1/stats")

        self.assertEqual(content_type, "text/plain; charset=utf-8")
        self.assertIn("status", body)
        self.assertIn("down_for", body)
        self.assertIn("ONGOING", body)
        self.assertEqual(self.keeper.calls, ["stats"])

    def test_stats_endpoint_renders_table_for_text_accept(self):
        body, content_type = self.get_raw(
            "/v1/stats",
            headers={"Accept": "text/plain"},
        )

        self.assertEqual(content_type, "text/plain; charset=utf-8")
        self.assertIn("status", body)
        self.assertIn("down_for", body)
        self.assertEqual(self.keeper.calls, ["stats"])

    def test_action_endpoints_call_matching_keeper_commands(self):
        actions = ["start", "stop"]

        for action in actions:
            response = self.post(f"/v1/actions/{action}")
            self.assertEqual(response["last_command"]["action"], action)

        self.assertEqual(self.keeper.calls, actions)

    def test_unknown_path_returns_404(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/v1/missing")

        self.assertEqual(error.exception.code, 404)
        self.assertFalse(json.loads(error.exception.read())["ok"])

    def test_get_action_returns_405(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/v1/actions/start")

        self.assertEqual(error.exception.code, 405)
        self.assertFalse(json.loads(error.exception.read())["ok"])

    def get(self, path, headers=None):
        request = Request(self.base_url + path, headers=headers or {})
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def get_raw(self, path, headers=None):
        request = Request(self.base_url + path, headers=headers or {})
        with urlopen(request, timeout=5) as response:
            return response.read().decode("utf-8"), response.headers["Content-Type"]

    def post(self, path):
        request = Request(self.base_url + path, method="POST")
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())


if __name__ == "__main__":
    unittest.main()
