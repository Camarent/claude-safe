"""done_when must be one shell command or `MANUAL: <check>`: a sentence is a plan error (cs-plan check)."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_rules


class DoneWhenManual(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.a = SimpleNamespace(dir=td.name)

    def shape(self, text):
        return plan_check._done_when_shape_problems(self.a, {"dir": "01-a", "done_when": text})

    def test_prose_done_when_is_a_plan_error(self):
        probs = self.shape("passes 20 runs in a row")
        self.assertEqual(len(probs), 1)
        self.assertIn("reads as a sentence", probs[0])
        self.assertIn("MANUAL:", probs[0])

    def test_prose_starting_with_a_command_word_is_a_plan_error(self):
        # decoy: `cat` is a real command word, but the sentence words make it prose
        self.assertTrue(self.shape("cat the log shows nothing"))

    def test_manual_check_is_accepted(self):
        self.assertEqual(self.shape("MANUAL: open the dashboard and check the chart renders"), [])

    def test_manual_without_text_is_a_plan_error(self):
        self.assertTrue(self.shape("MANUAL:"))
        self.assertTrue(self.shape("MANUAL:   "))

    def test_runnable_commands_are_accepted(self):
        # decoy: real commands that start with a plain word are not prose
        for cmd in ("pytest -q", "make check", "test -f out/report.json", "true"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.shape(cmd), [])

    def test_manual_skips_the_file_and_command_checks(self):
        # the file named in the MANUAL text is not a path the plan must produce
        ph = {"dir": "01-a", "done_when": "MANUAL: open out/report.json in a browser", "paths": [], "depends": [], "goal": "", "tasks": 0}
        probs = plan_check._phase_problems(self.a, ph, os.path.join(self.a.dir, "phases", "01-a"), [])[1]
        self.assertFalse([p for p in probs if "needs" in p or "not a shell" in p], probs)

    def test_manual_tag(self):
        self.assertEqual(plan_rules.manual_tag("MANUAL: check it"), "check it")
        self.assertEqual(plan_rules.manual_tag("MANUAL:"), "")
        self.assertIsNone(plan_rules.manual_tag("MANUAL check it"))
        self.assertIsNone(plan_rules.manual_tag("manual: check it"))
        self.assertIsNone(plan_rules.manual_tag("test -f MANUAL:x"))


if __name__ == "__main__":
    unittest.main()
