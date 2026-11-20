"""qrun: unittest and `pytest -q` skips are seen (exit 3). No test here waits on a clock; the timeout kill is covered by test_qrun.py."""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qrun
from tests.support.tmpdirs import tracked_tmpdir


class TestSkipsSeen(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        self.log = os.path.join(self.d, "q.log")

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, name, output, rc):
        out = Path(self.d, name + ".out")
        out.write_text(output, encoding="utf-8")
        script = Path(self.d, name)
        script.write_text(f"#!/bin/sh\ncat '{out}'\nexit {rc}\n", encoding="utf-8")
        script.chmod(0o755)
        return str(script)

    def run_q(self, *cmd):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = qrun.main(["--log", self.log, "--", *cmd])
        return rc, buf.getvalue().splitlines()

    def test_unittest_skipped_is_ok_outside_ci(self):
        exe = self.fake("ut0", "Ran 3 tests in 0.001s\n\nOK (skipped=2)\n", 0)
        with mock.patch.dict(os.environ, {qrun.ENV_FAIL_ON_SKIP: ""}):
            rc, out = self.run_q(exe)
        self.assertEqual(rc, 0)
        self.assertTrue(out[0].startswith("OK unittest: 3 run, 2 skipped"), out)

    def test_pytest_log_line_is_not_a_summary(self):
        self.assertIsNone(qrun.summary(["retry failed in 0.2s"]))

    def test_unittest_skipped_in_ci_is_exit_3(self):
        exe = self.fake("ut", "Ran 3 tests in 0.001s\n\nOK (skipped=1)\n", 0)
        with mock.patch.dict(os.environ, {qrun.ENV_FAIL_ON_SKIP: "1"}):
            rc, out = self.run_q(exe)
        self.assertEqual(rc, 3)
        self.assertTrue(out[0].startswith("SKIPPED/EMPTY: 1 skipped"), out)

    def test_unittest_ok_no_skip_is_ok(self):
        exe = self.fake("ut2", "Ran 3 tests in 0.001s\n\nOK\n", 0)
        rc, out = self.run_q(exe)
        self.assertEqual(rc, 0)
        self.assertTrue(out[0].startswith("OK unittest: 3 run, 0 skipped"), out)

    def test_pytest_q_skipped_is_exit_3(self):
        exe = self.fake("pq", "..s\n3 passed, 1 skipped in 0.12s\n", 0)
        rc, out = self.run_q(exe)
        self.assertEqual(rc, 3)
        self.assertTrue(out[0].startswith("SKIPPED/EMPTY: 1 skipped"), out)

    def test_pytest_eq_skipped_still_exit_3(self):
        exe = self.fake("pe", "=== 3 passed, 1 skipped in 0.12s ===\n", 0)
        rc, _ = self.run_q(exe)
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
