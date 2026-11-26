import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import worktree, worktree_merge
from tests.support.tmpdirs import tracked_tmpdir

STAGE = "stage-x"
TASK = "task-1"
SUBJECT = "feat(docs): set modes"


def git(d, *args):
    return subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, check=False)


class CommitAppliesBriefModeTest(unittest.TestCase):
    def setUp(self):
        # the job worktree (repo d) and the main checkout (a_dir, holds the brief) are separate dirs
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        self.main = tracked_tmpdir()
        self.a_dir = self.main.name
        git(self.d, "init")
        git(self.d, "config", "user.name", "T")
        git(self.d, "config", "user.email", "t@example.com")
        git(self.d, "config", "commit.gpgsign", "false")
        Path(self.d, "README").write_text("init")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-q", "-m", "init")
        patch = mock.patch.object(worktree_merge, "_parts", return_value=(None, "job/x", [("", self.d, self.d)]))
        patch.start()
        self.addCleanup(patch.stop)
        regen = mock.patch.object(worktree_merge, "_regen_docs_index")
        regen.start()
        self.addCleanup(regen.stop)

    def tearDown(self):
        self.tmp.cleanup()
        self.main.cleanup()

    def write_brief(self, text):
        brief = Path(self.a_dir, worktree.WORK_DIR, STAGE, f"{TASK}.md")
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(text)

    def committed_mode(self, path):
        return git(self.d, "ls-tree", "HEAD", path).stdout.split()[0]

    def test_mode_755_commits_100755(self):
        Path(self.d, "run.sh").write_text("#!/bin/sh\n")
        self.write_brief("Mode: run.sh 755\n")
        worktree.commit(self.a_dir, STAGE, TASK, SUBJECT)
        self.assertEqual(self.committed_mode("run.sh"), "100755")

    def test_mode_644_clears_exec_bit_in_index(self):
        Path(self.d, "data.txt").write_text("x")
        git(self.d, "add", "-A")
        git(self.d, "update-index", "--chmod=+x", "--", "data.txt")
        git(self.d, "commit", "-q", "-m", "exec data")
        self.assertEqual(self.committed_mode("data.txt"), "100755")
        os.chmod(Path(self.d, "data.txt"), 0o755)  # disk keeps the exec bit: only the -x call can clear it in the index
        Path(self.d, "data.txt").write_text("y")
        self.write_brief("Mode: data.txt 644\n")
        worktree.commit(self.a_dir, STAGE, TASK, SUBJECT)
        self.assertEqual(self.committed_mode("data.txt"), "100644")

    def test_disk_mode_is_not_changed(self):
        Path(self.d, "run.sh").write_text("#!/bin/sh\n")
        os.chmod(Path(self.d, "run.sh"), 0o600)
        self.write_brief("Mode: run.sh 755\n")
        worktree.commit(self.a_dir, STAGE, TASK, SUBJECT)
        self.assertEqual(stat.S_IMODE(os.stat(Path(self.d, "run.sh")).st_mode), 0o600)
        self.assertEqual(self.committed_mode("run.sh"), "100755")

    def test_mode_path_not_in_commit_is_ignored(self):
        Path(self.d, "other.txt").write_text("x")
        self.write_brief("Mode: missing.sh 755\n")
        worktree.commit(self.a_dir, STAGE, TASK, SUBJECT)
        self.assertEqual(self.committed_mode("other.txt"), "100644")

    def test_no_brief_commits_without_modes(self):
        Path(self.d, "run.sh").write_text("#!/bin/sh\n")
        worktree.commit(self.a_dir, STAGE, TASK, SUBJECT)
        self.assertEqual(self.committed_mode("run.sh"), "100644")


if __name__ == "__main__":
    unittest.main()
