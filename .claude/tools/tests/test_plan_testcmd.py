"""plan_testcmd: a Test: line with bare pytest warns when the project names another test command."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_testcmd

WRAPPED = "uv run --frozen pytest -q"
TID = "task-1"  # task id under test; a named constant keeps the asserts free of path-like literals


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
        return plan_testcmd.check_bare_test_runner(self.d, TID, self.ws)

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

    def tasks_md(self, test_line):
        self.task(test_line)
        with open(os.path.join(self.d, "TASKS.md"), "w", encoding="utf-8") as f:
            f.write("## Tasks\n- [ ] task-1 one - owner: haiku - review: wave W1, haiku\n\n## Graph\nwave 1: task-1\n")

    def ban(self, *bans):
        self.cfg(test_ban=list(bans))

    def test_banned_test_part_is_an_error_naming_the_use(self):
        self.ban({"pattern": "^pytest", "use": "scripts/dev.sh test"})
        self.tasks_md("Test: cs-qrun -- pytest x")
        _, probs = plan_check.check_stage(self.d, repo=self.ws)
        got = [p for p in probs if p.startswith("testban:")]
        self.assertEqual(len(got), 1)
        self.assertIn(TID, got[0])
        self.assertIn("scripts/dev.sh test", got[0])
        self.assertIn(got[0], plan_check.errors_of(probs))

    def test_banned_pattern_reaches_a_bare_command(self):
        self.ban({"pattern": "^pytest", "use": "scripts/dev.sh test"})
        self.task("Test: pytest tests/x.py")
        self.assertTrue(plan_testcmd.check_test_ban(self.d, TID, self.ws)[0].startswith("testban:"))

    def test_decoys_no_error(self):
        self.ban({"pattern": "^pytest", "use": "scripts/dev.sh test"})
        self.task("Test: cs-qrun -- scripts/dev.sh test")
        self.assertEqual(plan_testcmd.check_test_ban(self.d, TID, self.ws), [])
        self.cfg()
        self.task("Test: cs-qrun -- pytest x")
        self.assertEqual(plan_testcmd.check_test_ban(self.d, TID, self.ws), [])

    def test_malformed_test_ban_is_ignored(self):
        self.task("Test: cs-qrun -- pytest x")
        for raw in ("pytest", [7, "pytest"], [{"pattern": "^pytest"}], [{"pattern": "([", "use": "x"}]):
            self.cfg(test_ban=raw)
            self.assertEqual(plan_testcmd.check_test_ban(self.d, TID, self.ws), [])
        self.ban({"pattern": "([", "use": "x"}, {"pattern": "^pytest", "use": "scripts/dev.sh test"})
        self.assertEqual(len(plan_testcmd.check_test_ban(self.d, TID, self.ws)), 1)

    def test_qrun_shell_operator_after_dashes_is_an_error(self):
        for line in ("Test: cs-qrun -- ! grep -q x f", "Test: cs-qrun -- && ls out", "Test: `cs-qrun -- | cat`"):
            self.task(line)
            got = plan_testcmd.check_qrun_operator(self.d, TID)
            self.assertEqual(len(got), 1, line)
            self.assertTrue(got[0].startswith("qrun-op:"))
            self.assertIn("bash -c", got[0])
            self.assertEqual(plan_check.errors_of(got), got)

    def test_qrun_decoys_no_error(self):
        for line in ("Test: cs-qrun -- scripts/dev.sh test x.py", 'Test: cs-qrun -- bash -c "! grep -q x f"'):
            self.task(line)
            self.assertEqual(plan_testcmd.check_qrun_operator(self.d, TID), [], line)

    def test_qrun_operator_reported_once_per_task(self):
        self.task("Test: cs-qrun -- ! grep x f && cs-qrun -- ! grep y g")
        self.assertEqual(len(plan_testcmd.check_qrun_operator(self.d, TID)), 1)

    def test_bare_pytest_stays_a_warning_not_an_error(self):
        self.cfg(test_command_hint="make test", test_ban=[{"pattern": "^nomatch$", "use": "x"}])
        self.task("Test: pytest tests/x.py")
        got = plan_testcmd.check_bare_test_runner(self.d, TID, self.ws)
        self.assertTrue(got[0].startswith("testcmd:"))
        self.assertEqual(plan_check.errors_of(got), [])


if __name__ == "__main__":
    unittest.main()
