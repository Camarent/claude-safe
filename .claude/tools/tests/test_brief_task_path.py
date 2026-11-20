"""The dev and reviewer prompts name the task file by its absolute main-tree path (a dev looked for a relative .work path in its job worktree)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs
from pipeline.configuration import WORK_DIR


class _Run:
    def __init__(self, stage, d):
        self.stage = stage
        self.dir = d


class BriefTaskPathTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="brief-task-path-")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.abspath(tmp.name)

    def _prompts(self, stage, task_id):
        a = _Run(stage, self.root)
        task = {"id": task_id}
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=None):
            dev = briefs.prompt_for(a, task, "dev", None, "x1")
            rev = briefs.prompt_for(a, task, "reviewer", None, "x2")
        return dev, rev

    def test_plain_stage_prompt_has_absolute_path(self):
        want = os.path.join(self.root, WORK_DIR, "s", "task-1.md")
        dev, rev = self._prompts("s", "task-1")
        self.assertIn(f"take the task in {want} ", dev)
        self.assertIn("read it there, not in the worktree", dev)
        self.assertIn(f"the task in {want} against", rev)

    def test_phase_task_prompt_has_absolute_path(self):
        stage = "feat/phases/01-core"
        want = os.path.join(self.root, WORK_DIR, "feat", "phases", "01-core", "task-2.md")
        dev, rev = self._prompts(stage, "task-2")
        self.assertIn(f"take the task in {want} ", dev)
        self.assertIn(f"the task in {want} against", rev)


if __name__ == "__main__":
    unittest.main()
