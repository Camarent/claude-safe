"""plan_rules.check_open_edit: open-ended edit permission and a task spanning the project repo and a nested repo (synthetic task files)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_rules


class OpenEdit(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = td.name

    def task(self, text):
        with open(os.path.join(self.d, "task-1.md"), "w", encoding="utf-8") as f:
            f.write(text)
        return plan_rules.check_open_edit(self.d, "task-1", "workspace/")

    def test_open_ended_edit_permission_is_flagged(self):
        got = self.task("Predicted paths: src/a.js\nDone: find the renderer; if it lives outside Predicted paths, edit it\n")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("open-edit: task-1"))
        self.assertIn("own task", got[0])

    def test_plain_brief_is_clean(self):
        self.assertEqual(self.task("Predicted paths: src/a.js\nDone: the renderer shows kana\n"), [])

    def test_both_repos_in_one_task_is_flagged_only_with_the_prefix_rule_on(self):
        text = "Predicted paths: src/a.js, workspace/a/plugins/x.js\nDone: x\n"
        self.assertTrue(self.task(text)[0].startswith("repos: task-1"))
        self.assertEqual(plan_rules.check_open_edit(self.d, "task-1", ""), [])
        self.assertEqual(self.task("Predicted paths: workspace/a/a.js\nDone: x\n"), [])

    def test_both_are_warnings_not_errors(self):
        got = self.task("Predicted paths: src/a.js, workspace/a/b.js\nDone: edit outside the listed files\n")
        self.assertEqual(len(got), 2)
        self.assertEqual(plan_check.errors_of(got), [])


if __name__ == "__main__":
    unittest.main()
