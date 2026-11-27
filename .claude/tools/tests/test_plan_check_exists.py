"""plan_check.check_exists: a name the Goal/Done asks to add that already exists in the repo is an error. plan_check.check_cite_exists: a cite of a file that is neither in the tree nor predicted by the task or an earlier task is an error."""

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
        patcher = mock.patch.object(shutil, "which", return_value=None)
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


class CheckCiteExistsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(os.path.join(self.repo, "src"))
        with open(os.path.join(self.repo, "src", "a.py"), "w", encoding="utf-8") as f:
            f.write("".join(f"line {i}\n" for i in range(1, 30)) + "def helper_x():\n    pass\n")
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)
        patcher = mock.patch.object(shutil, "which", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, texts, tid="task-1"):
        """Write each task file of texts; ents: the ids in texts order, with the depends taken from the "depends" key."""
        ents = []
        for name, (body, depends) in texts.items():
            with open(os.path.join(self.d, name + ".md"), "w", encoding="utf-8") as f:
                f.write(body)
            ents.append({"id": name, "depends": depends})
        return plan_check.check_cite_exists(self.d, tid, self.repo, ents)

    def test_cite_of_absent_file_reported(self):
        probs = self.check({"task-1": ("# T\nGoal: see gone/x.py:4 for the parser\nPredicted paths: src/a.py\n", [])})
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("cite-missing: gone/x.py", probs[0])

    def test_cite_of_file_predicted_by_earlier_task_passes(self):
        # decoy: the same cite; task-1 depends on task-0, which lists the file in Predicted paths
        texts = {
            "task-0": ("# T0\nGoal: add new.py\nPredicted paths: new/new.py\n", []),
            "task-1": ("# T1\nGoal: use new/new.py:4 in the parser\nPredicted paths: src/a.py\n", ["task-0"]),
        }
        self.assertEqual(self.check(texts), [])

    def test_cite_of_file_predicted_without_depends_reported(self):
        # decoy: the same cite; task-1 does not depend on task-0, so the file is not guaranteed to exist
        texts: dict[str, tuple[str, list[str]]] = {
            "task-0": ("# T0\nGoal: add new.py\nPredicted paths: new/new.py\n", []),
            "task-1": ("# T1\nGoal: use new/new.py:4 in the parser\nPredicted paths: src/a.py\n", []),
        }
        probs = self.check(texts)
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("cite-missing: new/new.py", probs[0])

    def test_cite_of_existing_file_with_wrong_symbol_names_real_line(self):
        probs = self.check({"task-1": ("# T\nGoal: the parser `helper_x` src/a.py:3\nPredicted paths: src/a.py\n", [])})
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("helper_x is at 30", probs[0])
        self.assertNotIn("cite-missing", probs[0])

    def test_cite_of_existing_file_with_right_symbol_passes(self):
        probs = self.check({"t": ("# T\nGoal: the parser `helper_x` src/a.py:30\nPredicted paths: src/a.py\n", [])}, tid="t")
        self.assertEqual(probs, [])

    def test_pattern_none_and_plain_text_pass(self):
        probs = self.check({"t": ("# T\nGoal: add a test\nPattern: none\nPredicted paths: src/a.py\n", [])}, tid="t")
        self.assertEqual(probs, [])

    def test_bare_cite_of_file_in_subdir_is_cite_short(self):
        # M25: `a.py` names src/a.py only by search; the cite must give the full path
        probs = self.check({"task-1": ("# T\nPattern: `helper_x` in a.py:30\nPredicted paths: src/a.py\n", [])})
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("cite-short: a.py has no directory", probs[0])
        self.assertIn("src/a.py", probs[0])

    def test_bare_cite_of_two_files_is_cite_short(self):
        os.makedirs(os.path.join(self.repo, "other"))
        with open(os.path.join(self.repo, "other", "a.py"), "w", encoding="utf-8") as f:
            f.write("x\n")
        probs = self.check({"task-1": ("# T\nGoal: see a.py:1 for the parser\nPredicted paths: src/a.py\n", [])})
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("cite-short: a.py", probs[0])
        self.assertIn("other/a.py", probs[0])
        self.assertIn("src/a.py", probs[0])

    def test_bare_backticked_file_in_subdir_is_cite_short(self):
        probs = self.check({"task-1": ("# T\nGoal: the parser in `a.py`\nPredicted paths: src/a.py\n", [])})
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("cite-short: a.py", probs[0])

    def test_bare_cite_of_root_file_passes(self):
        with open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8") as f:
            f.write("readme\n")
        self.assertEqual(self.check({"solo": ("# T\nGoal: see README.md:1 for the parser\nPredicted paths: src/a.py\n", [])}, tid="solo"), [])

    def test_full_path_cite_passes(self):
        probs = self.check({"task-1": ("# T\nPattern: `helper_x` in src/a.py:30\nPredicted paths: src/a.py\n", [])})
        self.assertEqual(probs, [])


if __name__ == "__main__":
    unittest.main()
