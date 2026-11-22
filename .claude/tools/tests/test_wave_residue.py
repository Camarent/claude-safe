import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_wave
from pipeline.configuration import SQUASH_MSG_FILE
from pipeline.ctx import Ctx


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout


def commit_all(repo, msg):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", msg)


class SquashResidueTest(unittest.TestCase):
    """A job branch changes x.txt; an interrupted `git merge --squash` left x.txt staged in the main checkout."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="wave-residue-")
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name  # the workspace: holds .wt/<stage>/manifest.json
        self.repo = os.path.join(self.dir, "main")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as f:
            f.write("base\n")
        commit_all(self.repo, "base")
        self.base = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(self.repo, "checkout", "-q", "-b", "job/task-1")
        with open(os.path.join(self.repo, "x.txt"), "w", encoding="utf-8") as f:
            f.write("job\n")
        commit_all(self.repo, "job")
        git(self.repo, "checkout", "-q", self.base)
        manifest = {"task-1": {"branch": "job/task-1", "base": self.base, "root": self.repo, "repos": {}}}
        os.makedirs(os.path.join(self.dir, ".wt", "s"))
        with open(os.path.join(self.dir, ".wt", "s", "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        self.c = Ctx(SimpleNamespace(stage="s", dir=self.dir))
        os.makedirs(self.c.w, exist_ok=True)
        self.a = SimpleNamespace(stage="s", dir=self.dir)
        patch = mock.patch.object(cli_wave, "repo_path", return_value=self.repo)
        patch.start()
        self.addCleanup(patch.stop)

    def interrupted_squash(self):
        git(self.repo, "merge", "--squash", "job/task-1")  # stages x.txt and writes SQUASH_MSG into the git dir

    def squash_msg(self):
        return os.path.join(git(self.repo, "rev-parse", "--absolute-git-dir").strip(), SQUASH_MSG_FILE)

    def test_residue_of_an_unmerged_job_is_reset(self):
        self.interrupted_squash()
        self.assertTrue(os.path.exists(self.squash_msg()))
        self.assertEqual(cli_wave.reset_squash_residue(self.a, self.c), ["x.txt"])
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "x.txt")))
        self.assertIn("merge_residue_reset", open(self.c.log, encoding="utf-8").read())

    def test_other_dirty_file_leaves_the_checkout_untouched(self):
        self.interrupted_squash()
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as f:
            f.write("edited by hand\n")
        self.assertEqual(cli_wave.reset_squash_residue(self.a, self.c), [])
        self.assertIn("x.txt", git(self.repo, "status", "--porcelain"))
        self.assertIn("edited by hand", open(os.path.join(self.repo, "a.txt"), encoding="utf-8").read())
        self.assertTrue(os.path.exists(self.squash_msg()))

    def test_no_squash_message_means_no_reset(self):
        with open(os.path.join(self.repo, "x.txt"), "w", encoding="utf-8") as f:
            f.write("hand edit\n")
        self.assertEqual(cli_wave.reset_squash_residue(self.a, self.c), [])
        self.assertIn("x.txt", git(self.repo, "status", "--porcelain"))


if __name__ == "__main__":
    unittest.main()
