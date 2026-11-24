"""The dev brief names the job worktree root (current directory, paths under it) and copies the import/sys.path header of a test file in the task's Pattern."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs

FIXTURE_TEST = '''"""Fixture test file: its header is the import and sys.path block below."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs


def test_something():
    assert briefs
'''
HEADER_LINES = [
    "import os",
    "import sys",
    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))",
    "from pipeline import briefs",
]


class _Run:
    def __init__(self, stage, d):
        self.stage = stage
        self.dir = d


class BriefsHeaderTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="briefs-header-")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.abspath(tmp.name)
        os.makedirs(os.path.join(self.root, "tests"))
        with open(os.path.join(self.root, "tests", "test_fixture.py"), "w", encoding="utf-8") as f:
            f.write(FIXTURE_TEST)
        self.task_file = os.path.join(self.root, "task-1.md")

    def _task(self, pattern):
        with open(self.task_file, "w", encoding="utf-8") as f:
            f.write(f"Predicted paths: x.py\nPattern: {pattern}\nTest: true\n")

    def test_dev_prompt_names_worktree_root_as_current_directory(self):
        a = _Run("s", self.root)
        t = os.path.join(self.root, ".work", "s", "task-1.md")
        os.makedirs(os.path.dirname(t))
        with open(t, "w", encoding="utf-8") as f:
            f.write("Predicted paths: x.py\nPattern: x.py:1\n")
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=None):
            prompt = briefs.prompt_for(a, {"id": "task-1"}, "dev", None, "x1")
        self.assertIn("Your job worktree root is your current directory", prompt)
        self.assertIn("edit files only with paths relative to it or absolute paths under it", prompt)
        self.assertNotIn("relative paths only", prompt)

    def test_dev_prompt_says_work_only_in_the_job_worktrees(self):
        a = _Run("s", self.root)
        t = os.path.join(self.root, ".work", "s", "task-1.md")
        os.makedirs(os.path.dirname(t))
        with open(t, "w", encoding="utf-8") as f:
            f.write("Predicted paths: x.py\nTest: true\n")
        info = {"branch": "job/s-task-1", "repos": {"main": os.path.join(self.root, ".wt", "job")}, "base": "dev"}
        with (
            mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}),
            mock.patch("pipeline.briefs.job_info", return_value=info),
            mock.patch("pipeline.briefs.maincheck.snapshot"),
            mock.patch("pipeline.briefs.linkguard.snapshot"),
        ):
            prompt = briefs.prompt_for(a, {"id": "task-1"}, "dev", None, "x1")
        self.assertIn("Work ONLY in these job worktrees", prompt)
        self.assertIn("Never edit the main checkouts", prompt)
        self.assertIn(os.path.join(self.root, ".wt", "job"), prompt)

    def test_test_file_pattern_brief_has_its_import_and_sys_path_header(self):
        self._task("tests/test_fixture.py:9")
        note = briefs.pattern_header_note(self.root, self.task_file)
        for line in HEADER_LINES:
            self.assertIn(line, note)
        self.assertNotIn("def test_something", note)
        self.assertNotIn("Fixture test file", note)

    def test_test_file_pattern_header_reaches_dev_prompt(self):
        self._task("tests/test_fixture.py:9")
        a = _Run("s", self.root)
        t = os.path.join(self.root, ".work", "s", "task-1.md")
        os.makedirs(os.path.dirname(t))
        with open(self.task_file, encoding="utf-8") as src, open(t, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=None):
            prompt = briefs.prompt_for(a, {"id": "task-1"}, "dev", None, "x1")
        for line in HEADER_LINES:
            self.assertIn(line, prompt)

    def test_source_file_pattern_gets_no_header(self):
        with open(os.path.join(self.root, "tests", "code.py"), "w", encoding="utf-8") as f:
            f.write("import os\n\ndef f():\n    return os\n")
        self._task("tests/code.py:3")
        self.assertEqual(briefs.pattern_header_note(self.root, self.task_file), "")

    def test_pattern_path_outside_the_root_is_not_read(self):
        out = tempfile.TemporaryDirectory(prefix="briefs-outside-")
        self.addCleanup(out.cleanup)
        outside = os.path.join(out.name, "test_outside.py")
        with open(outside, "w", encoding="utf-8") as f:
            f.write(FIXTURE_TEST)
        for pattern in (outside, os.path.relpath(outside, self.root)):
            self._task(pattern + ":1")
            self.assertEqual(briefs.pattern_header_note(self.root, self.task_file), "", pattern)

    def test_pattern_path_with_nul_byte_gives_no_header(self):
        self._task("tests/test_x\x00.py:1")
        self.assertEqual(briefs.pattern_header_note(self.root, self.task_file), "")


if __name__ == "__main__":
    unittest.main()
