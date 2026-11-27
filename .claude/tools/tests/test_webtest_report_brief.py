"""webtest brief: it carries the branch HEAD sha as a `HEAD: <sha>` first-line rule; a repo git cannot read gives a note, never a crash."""

import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs

GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]


def _git(repo, *args):
    subprocess.run(["git", *GIT_ID, "-C", repo, *args], check=True, capture_output=True)


class WebtestHeadNoteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = os.path.join(tmp.name, "repo")
        os.mkdir(self.repo)
        _git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "f.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.sha = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        self.a = SimpleNamespace(dir=self.repo, stage="s1/p1")
        self.task = {"id": "final-webtest"}

    def test_brief_carries_head_sha_and_rule(self):
        prompt = briefs.webtest_prompt(self.a, self.task, "a1")
        self.assertIn(self.sha, prompt)
        self.assertIn("HEAD:", prompt)
        self.assertIn(f"The branch HEAD is {self.sha}: put a line HEAD: {self.sha} as the first line of your report", prompt)

    def test_no_sha_when_git_fails_gives_note(self):
        plain = tempfile.TemporaryDirectory()
        self.addCleanup(plain.cleanup)
        a = SimpleNamespace(dir=plain.name, stage="s1/p1")
        note = briefs.head_note(a)
        self.assertIn("could not be read", note)
        self.assertNotIn("HEAD: ", note)
        prompt = briefs.webtest_prompt(a, self.task, "a1")
        self.assertIn("could not be read", prompt)


if __name__ == "__main__":
    unittest.main()
