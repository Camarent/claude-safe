"""No empty cache files: an empty leads -rel file is not written and a missing one reads as empty; the task file has no Goal line equal to its title. Temp dirs only."""

import glob
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs, plan_check, plan_files
from pipeline.configuration import ENV_NO_LEADS
from tests.support.tmpdirs import tracked_mkdtemp

REL_TEXT = " Related docs and tests of these files: tests/test_a.py."


def fake_local(stdout):
    return mock.patch.object(briefs, "run_py", return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=""))


class LeadsRelFiles(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="synthetic-project-")
        os.makedirs(os.path.join(self.d, ".work", "s"))
        with open(os.path.join(self.d, "a.py"), "w", encoding="utf-8") as f:
            f.write("def f():\n    return 1\n")
        self.a = SimpleNamespace(dir=self.d, stage="s")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(ENV_NO_LEADS, None)
        self.rel_path = os.path.join(self.d, ".work", "s", "leads", "leads-k-rel.md")

    def test_non_empty_rel_is_written_and_read(self):
        with fake_local("a.py:1\n"), mock.patch.object(briefs, "related_files", return_value=REL_TEXT):
            text = briefs.leads_for(self.a, "k", "where is f")
        self.assertIn(REL_TEXT.strip(), text)
        with open(self.rel_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), REL_TEXT.strip())

    def test_empty_rel_is_not_written(self):
        with fake_local("a.py:1\n"), mock.patch.object(briefs, "related_files", return_value=""):
            text = briefs.leads_for(self.a, "k", "where is f")
        self.assertIn("Code leads", text)
        self.assertFalse(os.path.exists(self.rel_path))
        self.assertEqual(glob.glob(os.path.join(self.d, ".work", "s", "**", "*-rel.md"), recursive=True), [])

    def test_missing_rel_reads_as_empty(self):
        cache = os.path.join(self.d, ".work", "s", "leads", "leads-k.md")
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            f.write("a.py:1\n")
        with mock.patch.object(briefs, "related_files", return_value=""):
            text = briefs.leads_for(self.a, "k", "where is f")
        self.assertIn("a.py:1", text)
        self.assertNotIn("Related docs", text)
        self.assertFalse(os.path.exists(self.rel_path))


class GoalLine(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="synthetic-project-")
        self.task = os.path.join(self.d, "task-1.md")
        self.entry = {"id": "task-1", "title": "add the parser", "wave": 1}
        self.args = SimpleNamespace(test="pytest -q", pattern="", done="parser works", turns=20)

    def test_rendered_task_has_no_goal_line(self):
        text = plan_files.render_task(self.entry, self.args, ["src/p.py"])
        self.assertNotIn("Goal:", text)
        self.assertIn("# task-1: add the parser\n", text)

    def test_title_edit_keeps_no_duplicate_goal(self):
        text = plan_files.render_task(self.entry, self.args, ["src/p.py"])
        out = plan_files.set_task_field(text, "task-1", "title", "new name")
        self.assertIn("# task-1: new name\n", out)
        self.assertNotIn("Goal:", out)

    def test_different_goal_is_kept_and_read(self):
        text = "# task-1: add the parser\nGoal: fix 18 warnings in src/p.py\nDone: ok\n"
        with open(self.task, "w", encoding="utf-8") as f:
            f.write(text)
        self.assertEqual(plan_check.check_warning_count(self.d, "task-1"), [])
        with open(self.task, "w", encoding="utf-8") as f:
            f.write("# task-1: fix 30 warnings in src\nDone: ok\n")
        self.assertEqual(len(plan_check.check_warning_count(self.d, "task-1")), 1)

    def test_title_read_when_goal_absent(self):
        with open(self.task, "w", encoding="utf-8") as f:
            f.write("# task-1: fix 30 warnings in src\nDone: ok\n")
        self.assertEqual(len(plan_check.check_warning_count(self.d, "task-1")), 1)


if __name__ == "__main__":
    unittest.main()
