"""Merge dedup by tree hash (base ignored), and the conflicted hunks kept for the merger. Temp git repos only."""

import os
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOOLS)
import merge_wave as MW  # noqa: E402 - needs TOOLS on sys.path first
from pipeline import gate_record as G  # noqa: E402 - needs TOOLS on sys.path first
from pipeline import worktree as W  # noqa: E402 - needs TOOLS on sys.path first

IDENT = ("-c", "user.name=t", "-c", "user.email=t@t")


def git(cwd, *args, check=True):
    return subprocess.run(["git", *IDENT, "-C", cwd, *args], capture_output=True, text=True, check=check)


def write(repo, name, text):
    with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
        f.write(text)


def make_repo(root, files):
    os.makedirs(root)
    git(root, "init", "-q")
    for name, text in files.items():
        write(root, name, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


class PassedGatesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = make_repo(os.path.join(tmp.name, "r"), {"x.txt": "1"})

    def test_green_tree_skips_even_when_head_moved(self):
        G.record(self.repo, ["t"], 0)  # green on tree T1, base = first commit
        write(self.repo, "x.txt", "2")
        git(self.repo, "commit", "-q", "-am", "other")
        write(self.repo, "x.txt", "1")
        git(self.repo, "commit", "-q", "-am", "squash-like: same tree, new HEAD")
        self.assertIn("t", MW._passed_gates(self.repo, ["t"]))

    def test_changed_tree_reruns(self):
        G.record(self.repo, ["t"], 0)
        write(self.repo, "x.txt", "changed")
        self.assertEqual(MW._passed_gates(self.repo, ["t"]), {})

    def test_failed_record_never_skips(self):
        G.record(self.repo, ["t"], 1)
        self.assertEqual(MW._passed_gates(self.repo, ["t"]), {})


class ConflictHunksTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = make_repo(os.path.join(tmp.name, "c"), {"x.txt": "base\n"})
        git(self.repo, "branch", "job")
        git(self.repo, "checkout", "-q", "job")
        write(self.repo, "x.txt", "job side\n")
        git(self.repo, "commit", "-q", "-am", "job")
        git(self.repo, "checkout", "-q", "-")
        write(self.repo, "x.txt", "main side\n")
        git(self.repo, "commit", "-q", "-am", "main")

    def test_conflicted_hunks_listed_before_reset(self):
        sq = git(self.repo, "merge", "--squash", "job", check=False)
        self.assertNotEqual(sq.returncode, 0)
        hunks = W._conflict_hunks(self.repo)
        self.assertTrue(hunks.startswith("conflicted hunks:"))
        self.assertIn("x.txt", hunks)
        self.assertIn("job side", hunks)
        self.assertIn("main side", hunks)
        git(self.repo, "reset", "--hard", "HEAD")
