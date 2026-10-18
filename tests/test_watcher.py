import unittest
from unittest.mock import patch

import acgnx_watch as aw


class WatcherTests(unittest.TestCase):
    def item(self):
        rule = {"id": "r123456789", "name": "demo", "group": []}
        return {"_rid": rule["id"], "_rule": rule, "episode": "02",
                "btih": "a" * 40, "title": "demo", "_tier": 0,
                "_rev": 1, "_seen": "0:1", "pub_dt": None}

    def cycle(self, state, success=True):
        with patch.object(aw, "collect", return_value=[self.item()]), \
             patch.object(aw, "group_key", return_value="demo-group"), \
             patch.object(aw, "build_push_content", return_value=("title", "body")), \
             patch.object(aw, "send_notification", return_value=(success, "")) as send, \
             patch.object(aw, "save_state"):
            aw.run_cycle({"settings": {"first_run_mode": "latest", "push_min_interval_seconds": 0}}, state)
            return send.call_count

    def test_first_run_records_correct_group_key(self):
        state = {"initialized": False, "episodes": {}}
        self.assertEqual(self.cycle(state), 1)
        self.assertEqual(state["episodes"], {"r123456789|02|demo-group": "0:1"})
        self.assertTrue(state["initialized"])
        self.assertEqual(self.cycle(state), 0)

    def test_failed_notification_is_retried(self):
        state = {"initialized": True, "episodes": {}}
        self.assertEqual(self.cycle(state, False), 1)
        self.assertEqual(state["episodes"], {"r123456789|02|demo-group": "0:1"})
        self.assertEqual(len(state["pending_notifications"]), 1)
        self.assertEqual(self.cycle(state, True), 1)
        self.assertEqual(state["pending_notifications"], {})

    def test_persistent_episode_floor(self):
        for episode, expected in [("48", False), ("49", True), ("45-50", True), ("01-12", False), ("unknown", True)]:
            with self.subTest(episode=episode):
                self.assertEqual(aw.episode_allowed(episode, 49), expected)


if __name__ == "__main__":
    unittest.main()
