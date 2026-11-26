"""orch phases, group 3 of 4: see orch_harness.PhaseHarness (split from test_orch.py to keep each file fast)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orch_harness import PhaseHarness, start_index_patch, stop_index_patch


def setUpModule():
    start_index_patch()


def tearDownModule():
    stop_index_patch()


class PhaseTest(PhaseHarness, unittest.TestCase):
    def test_no_push_flag_skips_push_master(self):
        self.post("flags: no-push\n")
        self.go("skip", "--step", "master-merged")
        self.run_batch(["script"], "DONE")
        self.run_batch(["script"], "DONE")
        self.assertEqual(self.go("next")["action"], "done")

    def test_push_master_then_republish_then_index_then_live_check(self):
        self.post("flags: index\nPUBLISH: https://x/y\n")
        self.go("skip", "--step", "master-merged")
        d = self.run_batch(["ops"], "DONE")
        self.assertIn("push origin master", d[0]["prompt"])
        d = self.run_batch(["publisher"], "DONE")
        self.assertIn("PUBLISH:", d[0]["prompt"])
        self.run_batch(["script"], "DONE")
        self.run_batch(["web-tester"], "PASS")
        self.run_batch(["script"], "DONE")  # live-check-stop: servectl safety net
        self.run_batch(["script"], "DONE")
        self.assertEqual(self.go("next")["action"], "done")

    def test_no_publish_line_no_republish(self):
        self.post("flags: index\n")
        self.go("skip", "--step", "master-merged")
        self.run_batch(["ops"], "DONE")
        self.assertEqual(self.batch()[0]["agent"], "script")

    def test_push_master_fail_asks_main(self):
        self.post("")
        self.go("skip", "--step", "master-merged")
        self.run_batch(["ops"], "FAIL")
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("push-master", r["message"])

    def test_retry_from_review_reruns_only_reviewer(self):
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.go("retry", "--step", "task-1", "--from", "review")
        self.assertEqual([x["agent"] for x in self.batch()], ["reviewer"])

    def test_index_refresh_done_then_live_check(self):
        self.post("flags: index\nPUBLISH: https://x/y\n")
        self.go("skip", "--step", "master-merged")
        self.run_batch(["ops"], "DONE")
        self.run_batch(["publisher"], "DONE")
        self.run_batch(["script"], "DONE")
        ds = self.run_batch(["web-tester"], "PASS")
        self.assertIn("live-check.md", ds[0]["prompt"])
        self.assertIn("UNVERIFIED:", ds[0]["prompt"])
        self.run_batch(["script"], "DONE")  # live-check-stop: servectl safety net
        self.run_batch(["script"], "DONE")
        self.assertEqual(self.go("next")["action"], "done")

    def test_index_refresh_fail_is_only_a_warning(self):
        self.post("flags: index\n")
        self.go("skip", "--step", "master-merged")
        self.run_batch(["ops"], "DONE")
        self.run_batch(["script"], "FAIL")
        self.assertNotEqual(self.go("next").get("action"), "ask_main")  # the stage goes on (cleanup, retro) instead of asking the lead

    def test_live_check_fail_asks_main(self):
        self.post("PUBLISH: https://x/y\n")
        self.go("skip", "--step", "master-merged")
        self.run_batch(["ops"], "DONE")
        self.run_batch(["publisher"], "DONE")
        self.run_batch(["script"], "DONE")
        self.run_batch(["web-tester"], "FAIL")
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("live-check", r["message"])

    def test_live_check_blocked_asks_main(self):
        self.post("PUBLISH: https://x/y\n")
        self.go("skip", "--step", "master-merged")
        self.run_batch(["ops"], "DONE")
        self.run_batch(["publisher"], "DONE")
        self.run_batch(["script"], "DONE")
        self.run_batch(["web-tester"], "BLOCKED")
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("live-check", r["message"])


if __name__ == "__main__":
    unittest.main()
