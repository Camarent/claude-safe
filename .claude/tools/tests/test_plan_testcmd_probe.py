"""plan_testcmd: a bare pytest Test line is a testrunner: error when pytest does not import with python3 and no test command is named."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_testcmd
from pipeline.configuration import TESTRUNNER_PREFIX, TESTRUNNER_WORKING_FORM

TID = "task-1"  # task id under test; a named constant keeps the asserts free of path-like literals


def probe_ok() -> bool:
    return True


def probe_fails() -> bool:
    return False


PROBE_OK, PROBE_FAILS = probe_ok, probe_fails


class T(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.ws = td.name
        self.d = os.path.join(self.ws, ".work", "s")
        os.makedirs(self.d)

    def cfg(self, **kw):
        with open(os.path.join(self.ws, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump(kw, f)

    def task(self, test_line):
        with open(os.path.join(self.d, TID + ".md"), "w", encoding="utf-8") as f:
            f.write(f"Predicted paths: a.py\nTest: {test_line}\n")

    def check(self, probe):
        return plan_testcmd.check_bare_test_runner(self.d, TID, self.ws, probe=probe)

    def test_probe_ok_gives_no_problem(self):
        self.cfg()
        self.task("pytest tests/x.py")
        self.assertEqual(self.check(PROBE_OK), [])

    def test_probe_fails_on_pytest_line_is_a_testrunner_error(self):
        self.cfg()
        self.task("pytest tests/x.py")
        got = self.check(PROBE_FAILS)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(TESTRUNNER_PREFIX))
        self.assertIn(TESTRUNNER_WORKING_FORM, got[0])
        self.assertEqual(plan_check.errors_of(got), got)

    def test_probe_fails_on_python_dash_m_pytest_line_is_an_error(self):
        self.cfg()
        self.task("`python3 -m pytest -q tests/x.py`")
        self.assertEqual(len(self.check(PROBE_FAILS)), 1)

    def test_probe_fails_on_non_pytest_line_gives_no_problem(self):
        self.cfg()
        self.task("make test")
        self.assertEqual(self.check(PROBE_FAILS), [])

    def test_named_test_command_keeps_the_testcmd_error(self):
        self.cfg(test_command_hint="make test")
        self.task("pytest tests/x.py")
        got = self.check(PROBE_FAILS)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("testcmd:"))

    def test_probe_not_run_without_a_bare_pytest_line(self):
        self.cfg()
        self.task("make test")
        probe = mock.Mock(return_value=False)
        self.check(probe)
        probe.assert_not_called()

    def test_probe_runs_python_and_caches_its_result(self):
        plan_testcmd.pytest_importable.cache_clear()
        self.addCleanup(plan_testcmd.pytest_importable.cache_clear)
        done: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(args=(), returncode=1)
        with mock.patch.object(plan_testcmd.subprocess, "run", return_value=done) as run:
            self.assertFalse(plan_testcmd.pytest_importable())
            self.assertFalse(plan_testcmd.pytest_importable())
        run.assert_called_once()

    def test_probe_missing_python_counts_as_failure(self):
        plan_testcmd.pytest_importable.cache_clear()
        self.addCleanup(plan_testcmd.pytest_importable.cache_clear)
        with mock.patch.object(plan_testcmd.subprocess, "run", side_effect=OSError("no python3")):
            self.assertFalse(plan_testcmd.pytest_importable())


if __name__ == "__main__":
    unittest.main()
