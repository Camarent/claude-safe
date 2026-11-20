import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import ask_code
from tests.support.tmpdirs import tracked_mkdtemp


class T(unittest.TestCase):
    def test_root_from_nested_dir_with_git(self):
        d = os.path.realpath(tracked_mkdtemp())
        os.makedirs(f"{d}/.git")
        os.makedirs(f"{d}/.work/x")
        self.assertEqual(ask_code.find_root(f"{d}/.work/x"), d)

    def test_root_from_code_index_without_git(self):
        d = os.path.realpath(tracked_mkdtemp())
        os.makedirs(f"{d}/.code-index")
        os.makedirs(f"{d}/a/b")
        self.assertEqual(ask_code.find_root(f"{d}/a/b"), d)

    def test_stray_nested_code_index_loses_to_git(self):
        d = os.path.realpath(tracked_mkdtemp())
        os.makedirs(f"{d}/.git")
        os.makedirs(f"{d}/a/.code-index")
        os.makedirs(f"{d}/a/b")
        self.assertEqual(ask_code.find_root(f"{d}/a/b"), d)

    def test_worktree_git_file(self):
        d = os.path.realpath(tracked_mkdtemp())
        os.makedirs(f"{d}/w/s")
        Path(f"{d}/w/.git").write_text("gitdir: x", encoding="utf-8")
        self.assertEqual(ask_code.find_root(f"{d}/w/s"), f"{d}/w")

    def test_root_flag_overrides(self):
        from search import local

        d = os.path.realpath(tracked_mkdtemp())
        os.makedirs(f"{d}/A/.git")
        os.makedirs(f"{d}/B/.git")
        old = os.getcwd()
        os.chdir(f"{d}/B")
        try:
            self.assertEqual(local.with_root(["q"]), ["q", "--root", f"{d}/B"])
            self.assertEqual(local.with_root(["q", "--root", f"{d}/A"]), ["q", "--root", f"{d}/A"])
        finally:
            os.chdir(old)

    def test_work_dirs_excluded(self):
        for n in (".work", ".audits", ".worktrees"):
            self.assertIn(n, ask_code.SKIP_DIRS)


if __name__ == "__main__":
    unittest.main()
