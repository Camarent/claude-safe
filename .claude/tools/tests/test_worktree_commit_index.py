import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import util, worktree
from tests.support.tmpdirs import tracked_tmpdir

SUBJECT = "feat(docs): regenerate index"


def git(d, *args):
    return subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, check=False)


class CommitRegeneratesIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        git(self.d, "init")
        git(self.d, "config", "user.name", "T")
        git(self.d, "config", "user.email", "t@example.com")
        git(self.d, "config", "commit.gpgsign", "false")
        Path(self.d, "pipeline.json").write_text('{"docs_dir": "docs"}')
        Path(self.d, "docs").mkdir()
        Path(self.d, "docs", "modules.txt").write_text("")
        Path(self.d, "f.txt").write_text("x")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-q", "-m", "init")
        # the job worktree is the repo itself: commit() takes the (a_dir, stage, task) layout from _parts
        patch = mock.patch.object(worktree, "_parts", return_value=(None, "job/x", [("", self.d, self.d)]))
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def edit_code(self):
        Path(self.d, "f.txt").write_text("changed")

    def committed(self):
        return git(self.d, "show", "--name-only", "--format=", "HEAD").stdout.split()

    def index_text(self):
        return Path(self.d, "docs", "INDEX.md").read_text()

    def test_stale_index_is_regenerated_into_the_commit(self):
        Path(self.d, "docs", "INDEX.md").write_text("stale\n")
        self.edit_code()
        self.assertIsNotNone(worktree.commit(self.d, "s", "t", SUBJECT)["."])
        self.assertIn("docs/INDEX.md", self.committed())
        self.assertIn("# Index", self.index_text())
        self.assertNotIn("stale", self.index_text())

    def test_no_index_file_means_no_index_created(self):
        self.edit_code()
        worktree.commit(self.d, "s", "t", SUBJECT)
        self.assertFalse(Path(self.d, "docs", "INDEX.md").exists())
        self.assertNotIn("docs/INDEX.md", self.committed())
        self.assertIn("f.txt", self.committed())

    def test_generator_failure_commits_unchanged_and_is_logged(self):
        Path(self.d, "docs", "INDEX.md").write_text("stale\n")
        self.edit_code()
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with mock.patch.object(util, "run_py", return_value=failed), mock.patch.object(util, "swallowed") as swallowed:
            worktree.commit(self.d, "s", "t", SUBJECT)
        self.assertEqual(self.index_text(), "stale\n")
        self.assertIn("f.txt", self.committed())
        self.assertIn("docs/INDEX.md", self.committed())  # the existing file ships as it was, unregenerated
        self.assertEqual(swallowed.call_count, 1)
        self.assertEqual(swallowed.call_args.args[0], "worktree._regen_docs_index")

    def test_fresh_index_leaves_no_index_change(self):
        util.run_py("docs_index.py", "--root", self.d, "--docs-dir", "docs")
        self.assertIn("# Index", self.index_text())
        git(self.d, "add", "-A")
        git(self.d, "commit", "-q", "-m", "index")
        self.edit_code()
        worktree.commit(self.d, "s", "t", SUBJECT)
        self.assertEqual(self.committed(), ["f.txt"])


if __name__ == "__main__":
    unittest.main()
