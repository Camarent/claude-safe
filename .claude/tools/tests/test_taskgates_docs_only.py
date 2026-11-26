"""Task gates skip a docs-only change (GATE_SKIP_PATHS): the reason says the merge gate and CI still run the gates; one non-docs file runs them as before."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit import taskgates

SKIP_WHY = "docs-only, task gate skipped; merge gate and CI still run it"


def fake_git(changed, new=(), base_ok=True):
    """A git stub for the job worktree /job: `diff --name-only` gives CHANGED, `ls-files --others` gives NEW."""

    def git(_w, *args):
        if args[:2] == ("diff", "--name-only"):
            return "\n".join(changed) if base_ok else None
        if args[:2] == ("ls-files", "--others"):
            return "\n".join(new)
        return ""

    return git


class DocsOnlySkip(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        patches = [
            mock.patch.object(taskgates.wt, "live_job", return_value={"base": "stage/s"}),
            mock.patch.object(taskgates.wt, "_parts", return_value=({}, "job", [("", self.d, "/job")])),
            mock.patch.object(taskgates, "no_change", return_value=False),
            mock.patch.object(taskgates, "content_sha", return_value="sha1"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.cfg = {"worktrees": True}

    def reason(self, git):
        return taskgates._skip_reason(self.cfg, self.d, "s", "task-1", git)

    def test_only_markdown_changed_skips_the_task_gate_with_the_reason(self):
        why, sha = self.reason(fake_git(["README.md", "docs/notes.md"]))
        self.assertEqual((why, sha), (SKIP_WHY, ""))

    def test_new_untracked_markdown_counts_too(self):
        why = self.reason(fake_git(["README.md"], new=["docs/new.md"]))[0]
        self.assertEqual(why, SKIP_WHY)

    def test_one_non_docs_file_runs_the_gates_as_today(self):
        why, sha = self.reason(fake_git(["README.md", ".claude/tools/audit/taskgates.py"]))
        self.assertEqual((why, sha), ("", "sha1"))

    def test_one_non_docs_untracked_file_runs_the_gates(self):
        why = self.reason(fake_git(["README.md"], new=["new_module.py"]))[0]
        self.assertEqual(why, "")

    def test_no_changed_file_is_not_docs_only(self):
        self.assertFalse(taskgates.docs_only(self.d, "s", "task-1", fake_git([])))

    def test_git_that_cannot_answer_runs_the_gates(self):
        self.assertFalse(taskgates.docs_only(self.d, "s", "task-1", fake_git(["README.md"], base_ok=False)))

    def test_merge_gate_path_has_no_docs_skip(self):
        # the merge gate (merge_wave) runs every gate: the docs-only skip lives in taskgates only
        import merge_wave  # the path setup at the top of this file must run first

        self.assertFalse(hasattr(merge_wave, "docs_only"))
        self.assertFalse(hasattr(merge_wave, "GATE_SKIP_PATHS"))


if __name__ == "__main__":
    unittest.main()
