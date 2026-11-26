"""The dev brief says the job worktree root is the current directory and that edits use paths under it, never the old 'relative paths only' wording."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs

TASK_ID = "t1"


class _Run:
    def __init__(self, stage, d):
        self.stage = stage
        self.dir = d


class BriefWorktreeCwdTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="brief-cwd-")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.abspath(tmp.name)
        t = os.path.join(self.root, ".work", "s", TASK_ID + ".md")
        os.makedirs(os.path.dirname(t))
        with open(t, "w", encoding="utf-8") as f:
            f.write("Predicted paths: x.py\nTest: true\n")

    def _dev_prompt(self):
        wt = os.path.join(self.root, ".wt", "s", TASK_ID, "repo")
        info = {"branch": "job/s-" + TASK_ID, "repos": {"repo": wt}, "base": "dev"}
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=info):
            return briefs.prompt_for(_Run("s", self.root), {"id": TASK_ID}, "dev", None, "x1")

    def test_dev_prompt_names_worktree_root_as_current_directory(self):
        prompt = self._dev_prompt()
        self.assertIn("Your job worktree root is your current directory", prompt)
        self.assertIn(os.path.join(self.root, ".wt", "s", TASK_ID, "repo"), prompt)

    def test_dev_prompt_drops_relative_paths_only_wording(self):
        prompt = self._dev_prompt()
        self.assertNotIn("relative paths only", prompt)
        self.assertIn("paths relative to your worktree or absolute paths under it", prompt)


if __name__ == "__main__":
    unittest.main()
