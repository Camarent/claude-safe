"""plan_run.build_prompt: the architect brief carries the plan.py check checklist and tells the architect to run `check`."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_run
from pipeline.configuration import MODEL_SONNET

MARKER = "CHECKLIST"


class BuildPromptChecklist(unittest.TestCase):
    def setUp(self):
        self.a = SimpleNamespace(feature="f", dir=".")

    def prompt(self):
        return plan_run.build_prompt(self.a, MODEL_SONNET, "goal text")

    def test_prompt_holds_checklist_and_check_instruction(self):
        p = self.prompt()
        self.assertIn(MARKER, p)
        self.assertIn("Test line", p)
        self.assertIn("Affected tests", p)
        self.assertIn("Predicted paths", p)
        self.assertIn("depends", p)
        self.assertIn(f"`{plan_run.PLAN} check`", p)
        self.assertIn("goal text", p)

    def test_checklist_is_one_module_constant(self):
        self.assertIn(plan_run.BRIEF_CHECKLIST, self.prompt())

    def test_dropping_the_constant_removes_the_checklist(self):
        # mutation check: without the constant in build_prompt the marker is gone
        with mock.patch.object(plan_run, "BRIEF_CHECKLIST", ""):
            self.assertNotIn(MARKER, self.prompt())


if __name__ == "__main__":
    unittest.main()
