"""Changed-file lists from git must survive names with spaces and non-ASCII (lpcall.added_ranges, plan_context.modified_files)."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import plan_context as pc
from search import lpcall

NAME = "a b é.txt"


class ChangedNameTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        self.git("init", "-q")
        with open(os.path.join(self.repo, NAME), "w", encoding="utf-8") as fh:
            fh.write("one\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        with open(os.path.join(self.repo, NAME), "w", encoding="utf-8") as fh:
            fh.write("one\ntwo\n")
        self.git("commit", "-q", "-am", "change")
        self.head = self.git("rev-parse", "HEAD").strip()

    def git(self, *a):
        r = subprocess.run(
            ["git", "-c", "safe.directory=*", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", *a],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout

    def test_modified_files_keeps_spaces_and_non_ascii(self):
        self.assertEqual(pc.modified_files(self.repo, self.base, self.head), [NAME])

    def test_changed_files_keeps_spaces_and_non_ascii(self):
        self.assertEqual(lpcall.changed_files(self.repo, self.base, self.head), [NAME])

    def test_added_ranges_keeps_spaces_and_non_ascii(self):
        self.assertEqual(lpcall.added_ranges(self.repo, self.base, self.head), {NAME: [(2, 2)]})


if __name__ == "__main__":
    unittest.main()
