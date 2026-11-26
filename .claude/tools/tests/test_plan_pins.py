"""plan_pins.check_pinned_tests: a task that edits a data or setting file must list the test that pins it (the `pins:` error)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_pins, plan_rules
from pipeline.configuration import AFFECTED_MAX_TESTS, PINS_PREFIX
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp

TID = "task-1"  # one task id for every case; asserts use TID, not a literal


class PinnedTests(unittest.TestCase):
    def setUp(self):
        self.repo = tracked_mkdtemp()
        self.d = tracked_mkdtemp()
        git(self.repo, "init", "-q", "-b", "main")
        self.put("pipeline.json", "{}\n")
        self.put("src/engine.py", "x = 1\n")
        self.put("tests/test_config.py", "import json\nDATA = 'pipeline.json'\n")
        self.put("tests/test_engine.py", "from src import engine\n")

    def put(self, rel, text):
        p = Path(self.repo, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        git(self.repo, "add", rel)

    def check(self, text):
        Path(self.d, TID + ".md").write_text(text, encoding="utf-8")
        return plan_pins.check_pinned_tests(self.d, TID, self.repo)

    def test_a_data_file_edit_without_its_pinning_test_is_an_error(self):
        out = self.check("Predicted paths: pipeline.json\n")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith(PINS_PREFIX + " " + TID + ":"))
        self.assertIn("tests/test_config.py pins pipeline.json", out[0])
        self.assertEqual(plan_check.errors_of(out), out)

    def test_listing_the_pinning_test_in_predicted_paths_silences_it(self):
        self.assertEqual(self.check("Predicted paths: pipeline.json, tests/test_config.py\n"), [])

    def test_a_py_source_with_no_data_role_gives_no_error(self):
        self.assertEqual(self.check("Predicted paths: src/engine.py\n"), [])

    def test_a_py_file_under_configuration_or_named_with_a_word_list_or_default_is_data(self):
        self.put("configuration/planning.py", "X = 1\n")
        self.put("tests/test_planning.py", "import planning  # configuration/planning.py\n")
        self.assertEqual(len(self.check("Predicted paths: configuration/planning.py\n")), 1)
        self.put("words.py", "WORDS = []\n")
        self.put("tests/test_words.py", "import words  # words.py\n")
        self.assertEqual(self.check("Predicted paths: words.py\n"), [])
        self.assertEqual(len(self.check("Predicted paths: words.py\nGoal: change the default words.py list\n")), 1)

    def test_a_py_file_gets_its_data_role_only_from_goal_or_done_as_a_whole_word(self):
        self.put("util.py", "X = 1\n")
        self.put("tests/test_util.py", "import util  # util.py\n")
        self.assertEqual(self.check("Predicted paths: util.py\nGoal: fix the default timeout\n"), [])
        self.assertEqual(self.check("Predicted paths: util.py\nGoal: change util.py\nDone: defaults kept\n"), [])
        self.assertEqual(self.check("Predicted paths: util.py\nGoal: change util.py\nNotes: the default is kept\n"), [])
        self.assertEqual(len(self.check("Predicted paths: util.py\nGoal: change util.py\nDone: the default is kept\n")), 1)

    def test_a_test_file_or_a_folder_or_a_glob_is_not_pinned(self):
        self.assertEqual(self.check("Predicted paths: tests/test_config.py\n"), [])
        self.assertEqual(self.check("Predicted paths: *.json\n"), [])

    def test_report_tasks_are_skipped(self):
        self.assertEqual(self.check("Type: report\nPredicted paths: pipeline.json\n"), [])

    def test_a_file_mentioned_by_too_many_tests_gives_nothing(self):
        for i in range(AFFECTED_MAX_TESTS + 1):
            self.put(f"tests/test_use{i}.py", "import json\nDATA = 'pipeline.json'\n")
        self.assertEqual(self.check("Predicted paths: pipeline.json\n"), [])

    def test_a_nested_repo_pinning_test_is_named_with_its_path(self):
        nested = os.path.join(self.repo, "ws", "n")
        Path(nested, "tests").mkdir(parents=True)
        Path(nested, "tests", "test_cfg.py").write_text("X = 'settings.toml'\n", encoding="utf-8")
        git(nested, "init", "-q", "-b", "main")
        git(nested, "add", "tests/test_cfg.py")
        out = self.check("Predicted paths: ws/n/settings.toml\n")
        self.assertEqual(len(out), 1)
        self.assertIn("ws/n/tests/test_cfg.py", out[0])

    def test_wired_into_the_plan_check_errors_through_affected_tests(self):
        # check_affected_tests appends the pins findings; _task_file_problems keeps them as errors
        Path(self.d, TID + ".md").write_text("Predicted paths: pipeline.json\n", encoding="utf-8")
        e = {"id": TID, "wave": 1, "depends": []}
        got = plan_check._task_file_problems(self.d, e, [e], self.repo, [], None, [])
        pins = [p for p in got if p.startswith(PINS_PREFIX)]
        self.assertEqual(len(pins), 1)
        self.assertIn(pins[0], plan_check.errors_of(got))
        self.assertEqual(plan_rules.check_affected_tests(self.d, TID, self.repo), pins)


if __name__ == "__main__":
    unittest.main()
