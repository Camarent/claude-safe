"""plan_research_hint.check_research_hint: a Goal or Done with a trigger word and no research dependency gets a `research-hint:` warning; it never fails a check (errors_of)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_research_hint

RESEARCH = "task-1"  # the research task
CODE = "task-2"  # the task that needs the fact
HINT = "research-hint:"
GOAL_TRIGGER = "Goal: check whether madge supports typescript 7\nDone: C1: npm test passes\n"


class ResearchHintTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="synthetic-stage-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def task(self, name, body):
        with open(os.path.join(self.d, name + ".md"), "w", encoding="utf-8") as f:
            f.write(body)

    def ents(self, code_depends=()):
        return [
            {"id": RESEARCH, "depends": []},
            {"id": CODE, "depends": list(code_depends)},
        ]

    def research(self):
        self.task(RESEARCH, "Type: research\nOutputs: docs/madge.md\nGoal: write the findings\n")

    def test_trigger_without_research_dependency_warns(self):
        self.research()
        self.task(CODE, GOAL_TRIGGER)
        probs = plan_research_hint.check_research_hint(self.d, self.ents())
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith(f"{HINT} {CODE}:"))

    def test_research_dependency_passes(self):
        self.research()
        self.task(CODE, GOAL_TRIGGER)
        self.assertEqual(plan_research_hint.check_research_hint(self.d, self.ents((RESEARCH,))), [])

    def test_no_trigger_word_passes(self):
        self.research()
        self.task(CODE, "Goal: rename the helper in madge.py\nDone: C1: npm test passes\n")
        self.assertEqual(plan_research_hint.check_research_hint(self.d, self.ents()), [])

    def test_trigger_in_done_warns(self):
        self.research()
        self.task(CODE, "Goal: add the step\nDone: C1: the unknown flag is parsed\n")
        self.assertEqual(len(plan_research_hint.check_research_hint(self.d, self.ents())), 1)

    def test_research_task_itself_is_exempt(self):
        self.task(RESEARCH, "Type: research\nOutputs: docs/madge.md\nGoal: investigate the madge API\n")
        self.assertEqual(plan_research_hint.check_research_hint(self.d, [{"id": RESEARCH, "depends": []}]), [])

    def test_hint_is_a_warning_not_an_error(self):
        self.research()
        self.task(CODE, GOAL_TRIGGER)
        probs = plan_research_hint.check_research_hint(self.d, self.ents())
        self.assertEqual(len(probs), 1)
        self.assertEqual(plan_check.errors_of(probs), [])


if __name__ == "__main__":
    unittest.main()
