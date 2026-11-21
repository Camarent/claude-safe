"""plan_testcmd: a Test: line with bare pytest warns when the project names another test command."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_testcmd

WRAPPED = "uv run --frozen pytest -q"


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
        with open(os.path.join(self.d, "task-1.md"), "w", encoding="utf-8") as f:
            f.write(f"Predicted paths: a.py\n{test_line}\n")

    def check(self):
        return plan_testcmd.check_bare_test_runner(self.d, "task-1", self.ws)

    def test_bare_pytest_warns_with_setting_hint(self):
        self.cfg(test_command_hint="make test")
        self.task("Test: pytest tests/core/x.py")
        got = self.check()
        self.assertTrue(got and got[0].startswith("testcmd:") and "make test" in got[0])
        self.assertEqual(plan_check.errors_of(got), [])

    def test_python_dash_m_pytest_warns(self):
        self.cfg(test_command_hint="make test")
        self.task("Test: `python3 -m pytest -q tests/x.py`")
        self.assertEqual(len(self.check()), 1)

    def test_hint_derived_from_a_wrapped_gate(self):
        self.cfg(gates=[{"name": "t", "cmd": WRAPPED}])
        self.task("Test: pytest tests/x.py")
        self.assertIn(WRAPPED, self.check()[0])

    def test_no_warning_without_hint_or_with_the_named_command(self):
        self.cfg(gates=[{"name": "t", "cmd": "pytest -q"}])
        self.task("Test: pytest tests/x.py")
        self.assertEqual(self.check(), [])  # a bare gate names no other command
        self.cfg(test_command_hint="make test")
        self.task("Test: make test")
        self.assertEqual(self.check(), [])
        self.task("Goal: no test line")
        self.assertEqual(self.check(), [])


if __name__ == "__main__":
    unittest.main()
