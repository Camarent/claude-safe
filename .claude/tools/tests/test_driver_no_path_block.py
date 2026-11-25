"""Driver does not block a dev for changing a file outside its Predicted paths: the dev's DONE reaches the report, extra files are only recorded."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, driver
from pipeline.configuration import ENV_SCRATCH_DIR, VERDICT_PREFIX
from pipeline.util import work_path
from tests.support.tmpdirs import tracked_mkdtemp


class DevOutsidePredictedPaths(unittest.TestCase):
    def test_dev_done_with_a_file_outside_predicted_paths_is_not_blocked(self):
        w = tracked_mkdtemp()
        c = SimpleNamespace(w=w, stage="s", dir=w)
        d = {"id": "t1", "agent": "dev", "model": "m", "prompt": "p"}
        done = dict(claude.usage({}), error=False, stopped=False, result=f"{VERDICT_PREFIX}DONE\nchanged outside predicted paths: other/file.py")
        with (
            mock.patch.object(driver, "session_cwd", return_value=w),
            mock.patch.object(driver, "scratch_env", return_value={ENV_SCRATCH_DIR: w}),
            mock.patch.object(driver, "guard_env", return_value={}),
            mock.patch.object(claude, "claude_args", return_value=[]),
            mock.patch.object(claude, "session", return_value=done) as sess,
            mock.patch.object(driver, "orch", return_value={"verdict": "DONE"}) as rep,
        ):
            got_d, u, res = driver.run_one(c, d)
        self.assertIs(got_d, d)
        self.assertFalse(u["error"])
        self.assertNotIn("BLOCKED", str(u["result"]))
        self.assertEqual(res, {"verdict": "DONE"})
        sess.assert_called_once()
        with open(work_path(w, "report-t1.md"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith(VERDICT_PREFIX + "DONE"))
        rep.assert_called_once_with("report", "s", w, "--id", "t1")


if __name__ == "__main__":
    unittest.main()
