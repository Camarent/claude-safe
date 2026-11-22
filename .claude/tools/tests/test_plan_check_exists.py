"""plan_check.check_exists: a name the Goal/Done asks to add that already exists in the repo is an error."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check


class CheckExistsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(os.path.join(self.repo, "src"))
        with open(os.path.join(self.repo, "src", "a.py"), "w", encoding="utf-8") as f:
            f.write("def helper_x():\n    pass\n")
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)
        # no `lp` in these tests: the regex search over Predicted paths is what is checked
        patcher = mock.patch.object(plan_check.shutil, "which", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def task(self, text):
        with open(os.path.join(self.d, "task-1.md"), "w", encoding="utf-8") as f:
            f.write(text)
        return plan_check.check_exists(self.d, "task-1", self.repo)

    def test_existing_def_reported_with_line(self):
        probs = self.task("# T\nGoal: add `helper_x` to src/a.py\nPredicted paths: src/a.py\n")
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("already exists at src/a.py:1", probs[0])

    def test_existing_file_reported(self):
        probs = self.task("# T\nGoal: create src/a.py for the parser\nPredicted paths: src/a.py\n")
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("already exists at src/a.py:1", probs[0])

    def test_absent_name_passes(self):
        # decoy: same file, same verb, a name that is not in the repo
        self.assertEqual(self.task("# T\nGoal: add `helper_y` to src/a.py\nPredicted paths: src/a.py\n"), [])

    def test_absent_file_passes(self):
        self.assertEqual(self.task("# T\nGoal: create src/b.py for the parser\nPredicted paths: src/b.py\n"), [])

    def test_plain_word_is_not_a_name(self):
        # "add a test" names no file or symbol: nothing to search for
        self.assertEqual(self.task("# T\nGoal: add a test for helper_x\nPredicted paths: src/a.py\n"), [])


if __name__ == "__main__":
    unittest.main()
