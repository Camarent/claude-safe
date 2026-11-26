"""A script step that hits its time limit: the acceptance step is BLOCKED `timed out, not verified` with one slow-suite row; every other step stays FAIL. No real wait: Popen.communicate raises TimeoutExpired at once."""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import driver, slow_suite
from pipeline.configuration import ACCEPTANCE_STEP, RETRO_DIR, SLOW_SUITE_FILE, VERDICT_PREFIX
from tests.support.tmpdirs import tracked_mkdtemp


def _timed_out_popen():
    """-> a Popen stand-in whose first communicate times out; the cleanup communicate after the kill returns."""
    proc = mock.MagicMock()
    proc.communicate.side_effect = [subprocess.TimeoutExpired("script", 1), ("", "")]
    return mock.patch.object(driver.subprocess, "Popen", return_value=proc)


class ScriptTimeoutBlockedTest(unittest.TestCase):
    def setUp(self):
        self.stage = tracked_mkdtemp()
        self.cwd = os.path.join(tracked_mkdtemp(), "proj")
        os.makedirs(self.cwd)
        kill = mock.patch.object(driver.claude, "kill_group")
        kill.start()
        self.addCleanup(kill.stop)

    def _rows(self):
        path = os.path.join(self.stage, RETRO_DIR, SLOW_SUITE_FILE)
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_acceptance_timeout_is_blocked_not_verified_with_a_slow_suite_row(self):
        with _timed_out_popen():
            r = driver.run_script(f"{driver.SCRIPT_PREFIX}qrun.py", self.cwd, timeout=7, step=ACCEPTANCE_STEP, stage_dir=self.stage)
        self.assertTrue(r["result"].startswith(f"{VERDICT_PREFIX}BLOCKED\nwave: {slow_suite.REASON}: 7s"), r["result"])
        self.assertNotIn("FAIL", r["result"])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["step"], rows[0]["project"], rows[0]["limit"]), (ACCEPTANCE_STEP, "proj", 7))
        self.assertIn("seconds", rows[0])

    def test_other_step_timeout_stays_a_fail_and_writes_no_row(self):
        with _timed_out_popen():
            r = driver.run_script(f"{driver.SCRIPT_PREFIX}qrun.py", self.cwd, timeout=7, step="ops", stage_dir=self.stage)
        self.assertTrue(r["result"].startswith(f"{VERDICT_PREFIX}FAIL\nwave: script timed out after 7s"), r["result"])
        self.assertFalse(os.path.exists(os.path.join(self.stage, RETRO_DIR, SLOW_SUITE_FILE)))


if __name__ == "__main__":
    unittest.main()
