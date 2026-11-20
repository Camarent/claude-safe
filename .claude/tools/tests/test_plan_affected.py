"""plan_rules.check_affected_tests: the `tests-use:` warning for tests that use a predicted file but are not named in the task."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx, plan_check, plan_rules
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp


class AffectedTest(unittest.TestCase):
    def setUp(self):
        self.repo = tracked_mkdtemp()
        self.d = tracked_mkdtemp()
        git(self.repo, "init", "-q", "-b", "main")
        self.put("src/engine.py", "x = 1\n")
        self.put("tests/test_engine.py", "from src import engine\n")
        self.put("tests/test_other.py", "nothing here\n")

    def put(self, rel, text):
        p = Path(self.repo, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        git(self.repo, "add", rel)

    def check(self, text, repo="same"):
        Path(self.d, "task-1.md").write_text(text, encoding="utf-8")
        return plan_rules.check_affected_tests(self.d, "task-1", self.repo if repo == "same" else repo)

    def test_a_test_that_uses_a_predicted_file_but_is_not_named_is_a_warning(self):
        out = self.check("Predicted paths: src/engine.py\n")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("tests-use: task-1:"))
        self.assertIn("tests/test_engine.py", out[0])
        self.assertNotIn("test_other", out[0])

    def test_naming_the_test_anywhere_in_the_task_silences_it(self):
        self.assertEqual(self.check("Predicted paths: src/engine.py\nAffected tests: tests/test_engine.py\n"), [])
        self.assertEqual(self.check("Predicted paths: src/engine.py\nTest: pytest test_engine.py\n"), [])
        self.assertEqual(self.check("Predicted paths: src/engine.py, tests/test_engine.py\n"), [])

    def test_report_tasks_and_test_only_tasks_are_skipped(self):
        self.assertEqual(self.check("Type: report\nPredicted paths: src/engine.py\n"), [])
        self.assertEqual(self.check("Predicted paths: tests/test_engine.py\n"), [])

    def test_a_directory_a_glob_a_short_name_and_no_repo_give_nothing(self):
        self.put("src/io.py", "y = 2\n")
        self.put("tests/test_io.py", "import io\n")
        self.assertEqual(self.check("Predicted paths: src/io.py\n"), [])
        self.assertEqual(self.check("Predicted paths: src/\n"), [])
        self.assertEqual(self.check("Predicted paths: src/*.py\n"), [])
        self.assertEqual(self.check("Predicted paths: src/engine.py\n", repo=None), [])

    def test_a_name_mentioned_by_too_many_tests_gives_nothing(self):
        for i in range(plan_rules.AFFECTED_MAX_TESTS + 1):
            self.put(f"tests/test_use{i}.py", "import engine\n")
        self.assertEqual(self.check("Predicted paths: src/engine.py\n"), [])

    def test_data_docs_and_styles_are_not_matched_by_name(self):
        self.put("data/engine.jsonl", "{}\n")
        self.put("docs/engine.md", "x\n")
        self.put("tests/notes_test.md", "engine\n")
        self.assertEqual(self.check("Predicted paths: data/engine.jsonl\n"), [])
        out = self.check("Predicted paths: src/engine.py\n")
        self.assertEqual(len(out), 1)
        self.assertNotIn("notes_test", out[0])

    def test_a_git_timeout_or_missing_git_gives_no_warning_not_a_crash(self):
        for err in (subprocess.TimeoutExpired("git", 1), OSError("no git")):
            with mock.patch.object(gitx, "run", side_effect=err):
                self.assertEqual(self.check("Predicted paths: src/engine.py\n"), [])

    def test_a_nested_repo_is_searched_and_its_tests_are_named_with_their_path(self):
        nested = os.path.join(self.repo, "ws", "n")
        Path(nested, "tests").mkdir(parents=True)
        git(nested, "init", "-q", "-b", "main")
        Path(nested, "tests", "test_widget.py").write_text("import widget\n", encoding="utf-8")
        git(nested, "add", "tests/test_widget.py")
        out = self.check("Predicted paths: ws/n/src/widget.py\n")
        self.assertEqual(len(out), 1)
        self.assertIn("ws/n/tests/test_widget.py", out[0])

    def test_it_never_fails_a_check(self):
        out = self.check("Predicted paths: src/engine.py\n")
        self.assertEqual(plan_check.errors_of(out), [])


if __name__ == "__main__":
    unittest.main()
