"""A dev BLOCKED for a tests or environment reason with a commit: one verify script step (Test lines, then gates). Green goes to review with no ask; red goes back to the dev with the output; no commit still asks the lead."""

import os
import sys
import unittest
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps, steps_verify
from tests.test_no_fix_webtest import _Base

TID = "task-1"  # task id; asserts use this name, not the literal (path-dependent check)
TASK = {"id": TID, "owner_sonnet": False, "review_sonnet": False}
TESTS_RED = "VERDICT: BLOCKED\nBLOCKED: the test run cannot start: pytest is not installed\n"


def ev(kind, **kw):
    return {"ev": kind, "task": "task-1", **kw}


class BlockedDevVerifyTest(_Base):
    def setUp(self):
        super().setUp()
        self.task_file("# task-1: smoke\nTest: python3 check.py\n")
        self.diff = mock.patch.object(steps, "job_diff", return_value="diff --git a/x b/x\n")
        self.diff.start()
        self.addCleanup(self.diff.stop)

    def dev_run(self, text, verdict="BLOCKED"):
        return [
            ev("dispatch", id="a1", agent="dev", role="dev", model="haiku", why=None),
            ev("report", id="a1", role="dev", verdict=verdict, reason="pytest is not installed", why="pytest is not installed", file=self.report("r.md", text)),
        ]

    def verify_run(self, verdict, text):
        return [
            *self.dev_run(TESTS_RED),
            ev("dispatch", id="v1", agent="script", role="script", model=None, why=steps_verify.VERIFY_WHY, prompt="x"),
            ev("report", id="v1", role="script", verdict=verdict, file=self.report("v.md", text)),
        ]

    def test_blocked_for_checks_with_commit_dispatches_the_verify_step(self):
        r = self.nxt(self.dev_run(TESTS_RED))
        self.assertEqual((r["action"], r["role"], r["why"]), ("dispatch", "script", steps_verify.VERIFY_WHY))
        self.assertIn(f"steps_verify.py --stage s --task {TID}", r["prompt"])

    def test_blocked_for_checks_without_commit_still_asks(self):
        with mock.patch.object(steps, "job_diff", return_value=""):
            r = self.nxt(self.dev_run(TESTS_RED))
        self.assertEqual(r["action"], "ask_main")

    def test_blocked_owner_decision_does_not_run_the_verify_step(self):
        r = self.nxt(self.dev_run("VERDICT: BLOCKED\nBLOCKED: which of the two APIs should the brief use? owner decision\n"))
        self.assertEqual(r["action"], "ask_main")

    def test_green_verify_goes_to_review_without_an_ask(self):
        r = self.nxt(self.verify_run("DONE", "VERDICT: DONE\nTest lines and gates green\n"))
        self.assertNotEqual(r["action"], "ask_main")
        self.assertEqual((r["action"], r["role"]), ("dispatch", "reviewer"))

    def test_red_verify_sends_the_dev_back_with_the_output(self):
        r = self.nxt(self.verify_run("FAIL", "VERDICT: FAIL\nflake8: E501 line too long\n"))
        self.assertEqual((r["action"], r["role"]), ("dispatch", "dev"))
        self.assertIn("v.md", r["prompt"])  # the dev's prompt names the verify output file it reworks from

    def test_blocked_verify_asks_the_lead(self):
        r = self.nxt(self.verify_run("BLOCKED", "VERDICT: BLOCKED\nno job worktree\n"))
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("checks cannot run", r["message"])


class VerifyRunTest(unittest.TestCase):
    def test_red_test_lines_stop_before_the_gates(self):
        with mock.patch("testrun.run", return_value=("FAIL", "pytest: 1 failed")) as tr, mock.patch("audit.taskgates.run") as tg:
            v, note = steps_verify.run("s", "task-1", "/d")
        self.assertEqual((v, note), ("FAIL", "pytest: 1 failed"))
        tr.assert_called_once_with("s", "task-1", "/d")
        tg.assert_not_called()

    def test_skipped_test_line_is_blocked_not_green(self):
        with mock.patch("testrun.run", return_value=("DONE", "skipped: no Test line")), mock.patch("audit.taskgates.run") as tg:
            v, _ = steps_verify.run("s", "task-1", "/d")
        self.assertEqual(v, "BLOCKED")
        tg.assert_not_called()

    def test_green_tests_then_red_gates_return_the_gate_output(self):
        with mock.patch("testrun.run", return_value=("DONE", "ok")), mock.patch("audit.taskgates.run", return_value=("FAIL", "ruff: F401")):
            self.assertEqual(steps_verify.run("s", TID, "/d"), ("FAIL", "ruff: F401"))

    def test_green_tests_and_gates_are_done(self):
        with mock.patch("testrun.run", return_value=("DONE", "ok")), mock.patch("audit.taskgates.run", return_value=("DONE", "gates green")):
            self.assertEqual(steps_verify.run("s", TID, "/d"), ("DONE", "gates green"))

    def test_no_gates_configured_the_green_test_lines_decide(self):
        with mock.patch("testrun.run", return_value=("DONE", "ok")), mock.patch("audit.taskgates.run", return_value=("BLOCKED", "no `gates`")):
            v, _ = steps_verify.run("s", "task-1", "/d")
        self.assertEqual(v, "DONE")


if __name__ == "__main__":
    unittest.main()
