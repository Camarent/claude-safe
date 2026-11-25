"""plan.py check rules for briefs: absolute main-checkout Test paths, nested-repo paths in dev tasks."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_rules
from pipeline.configuration import TOOLS_DIR


class BriefRules(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)

    def write(self, name, text):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_check_stage_rejects_main_checkout_test_path(self):
        self.write("TASKS.md", "## Tasks\n- [ ] task-1 one - owner: haiku - review: wave W1, haiku\n\n## Graph\nwave 1: task-1\n")
        self.write("task-1.md", f"Predicted paths: src/a.py\nTest: python3 -I {TOOLS_DIR}/qrun.py -- node --test {self.root}/src/a.test.js\n")
        _, probs = plan_check.check_stage(self.d, repo=self.root)
        self.assertTrue(any(p.startswith("task-1:") and "main checkout" in p for p in probs), probs)

    def test_dev_task_nested_repo_path_rejected(self):
        self.write("t1.md", "Predicted paths: workspace/course/a.py\n")
        probs = plan_rules.check_nested_repo_paths(self.d, "t1", None, "workspace/")
        self.assertEqual(len(probs), 1)
        self.assertIn("t1: Predicted path workspace/course/a.py is in a nested repo", probs[0])

    def test_nested_repo_path_allowed_when_rule_off(self):
        self.write("t1.md", "Predicted paths: workspace/course/a.py\n")
        self.assertEqual(plan_rules.check_nested_repo_paths(self.d, "t1"), [])

    def test_dev_task_nested_repo_course_path_allowed(self):
        os.makedirs(os.path.join(self.root, "workspace", "course", ".git"))
        self.write("t1.md", "Predicted paths: workspace/course/a.py\n")
        self.assertEqual(plan_rules.check_nested_repo_paths(self.d, "t1", self.root, "workspace/"), [])

    def test_lead_step_course_path_allowed(self):
        self.write("t1.md", "owner: ops\nPredicted paths: workspace/course/a.py\n")
        self.assertEqual(plan_rules.check_nested_repo_paths(self.d, "t1", None, "workspace/"), [])

    def test_repo_path_not_course(self):
        self.write("t1.md", "Predicted paths: src/workspace.py\n")
        self.assertEqual(plan_rules.check_nested_repo_paths(self.d, "t1", None, "workspace/"), [])


ARCHITECT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agents", "architect.md")


class ArchitectBriefRules(unittest.TestCase):
    """architect.md carries the brief rules: each phrase must be present in that agent file, not in architect/*.md."""

    def setUp(self):
        with open(ARCHITECT, encoding="utf-8") as f:
            self.text = f.read()

    def test_grep_every_rule_present(self):
        self.assertIn("grep every", self.text.lower())

    def test_done_external_signal_rule_present(self):
        self.assertIn("what code must change", self.text)

    def test_predicted_paths_recommendation_present(self):
        self.assertIn("Predicted paths are a recommendation", self.text)

    def test_cite_missing_and_testban_named(self):
        self.assertIn("cite-missing:", self.text)
        self.assertIn("testban:", self.text)


if __name__ == "__main__":
    unittest.main()
