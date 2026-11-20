"""Characterization tests for docs_index: build_index layout, scan, check, references, git_candidates, main."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docs_index as D


def write(root, rel, text="x = 1\n"):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    Path(p).write_text(text, encoding="utf-8")


class Tmp(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name


class Index(Tmp):
    def test_layout_symbols_collapse_and_omit(self):
        write(self.d, "src/a.py", "def one():\n    pass\nclass Two:\n    pass\n")
        many = "".join(f"def f{i}(): pass\n" for i in range(14))
        write(self.d, "src/big.py", many)
        for i in range(9):  # more than COLLAPSE_OVER json files in one dir collapse to one line
            write(self.d, f"src/data/d{i}.json", "{}\n")
        write(self.d, "src/skip/__pycache__/x.py", "def hidden(): pass\n")
        write(self.d, "src/.hid/y.py", "def hidden2(): pass\n")
        write(self.d, "src/notes.txt", "not a source ext\n")
        idx = D.build_index(self.d).splitlines()
        self.assertEqual(idx[0], "# Index")
        self.assertEqual(idx[4], "## src  (`src`)")
        self.assertEqual(idx[6], "11 files, 27 lines")
        self.assertIn("- `src/data/*.json` (9 files, 9 lines)", idx)
        self.assertIn("- `src/a.py` (4) - one, Two", idx)
        self.assertIn("- `src/big.py` (14) - " + ", ".join(f"f{i}" for i in range(12)) + ", +2 more", idx)
        self.assertNotIn("hidden", "\n".join(idx))

    def test_files_omitted_over_cap(self):
        write(self.d, "m/a.py", "def a(): pass\n")
        write(self.d, "m/b.py", "def b(): pass\n")
        write(self.d, "m/c.py", "")
        with mock.patch.object(D, "MAX_FILES_PER_MODULE", 2):
            idx = D.build_index(self.d)
        self.assertIn("- ... 1 smaller files omitted", idx)

    def test_modules_txt(self):
        write(self.d, "docs/modules.txt", "# comment\n/beta/\nalpha\n")
        write(self.d, "alpha/a.py")
        write(self.d, "beta/b.py")
        write(self.d, "gamma/c.py")
        self.assertEqual(D.modules_of(self.d), ["alpha", "beta"])

    def test_scan_and_slug(self):
        write(self.d, "a.py", "async def go(): pass\nclass K: pass\nx = 1\n")
        self.assertEqual(D.scan(self.d, "a.py"), (3, ["go", "K"]))
        self.assertEqual(D.scan(self.d, "missing.py"), (0, []))
        write(self.d, "b.md", "# hi\n")
        self.assertEqual(D.scan(self.d, "b.md"), (1, []))
        self.assertEqual(D.slug("/a/b/"), "a--b")

    def test_ignored_paths_edge(self):
        self.assertEqual(D.ignored_paths(self.d, []), set())
        with mock.patch.object(D.gitx.subprocess, "run", side_effect=OSError):
            self.assertEqual(D.ignored_paths(self.d, ["x"]), set())


class Check(Tmp):
    def test_problems(self):
        write(self.d, "src/a.py", "l1\nl2\n")
        write(self.d, "docs/modules/src.md", "see src/a.py:2 and a.py:1-2 and a.py:5 and nope.py:1\n")
        write(self.d, "docs/modules/ghost.md", "x\n")
        write(self.d, "docs/INDEX.md", "src/a.py:99\n")
        self.assertEqual(
            D.check(self.d),
            [
                "missing docs/ARCHITECTURE.md",
                "docs/modules/ghost.md describes no existing module",
                "docs/modules/src.md: reference a.py:5 - file has only 2 lines",
                "docs/modules/src.md: reference nope.py:1 - file not found",
            ],
        )

    def test_module_without_doc_and_ok(self):
        write(self.d, "src/a.py")
        self.assertEqual(D.check(self.d), ["missing docs/ARCHITECTURE.md", "module 'src' has no docs/modules/src.md"])
        write(self.d, "docs/ARCHITECTURE.md", "arch\n")
        write(self.d, "docs/modules/src.md", "fine\n")
        self.assertEqual(D.check(self.d), [])


class GitAndMain(Tmp):
    def run_main(self, *args):
        buf = io.StringIO()
        code: str | int | None = 0
        with mock.patch.object(sys, "argv", ["docs_index", *args]), contextlib.redirect_stdout(buf):
            try:
                D.main()
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_git_candidates(self):
        os.makedirs(os.path.join(self.d, "r1", ".git"))
        os.makedirs(os.path.join(self.d, "a", "r2", ".git"))
        os.makedirs(os.path.join(self.d, "a", "b", "c", "r3", ".git"))
        os.makedirs(os.path.join(self.d, "node_modules", "r4", ".git"))
        self.assertEqual(D.git_candidates(self.d), sorted([os.path.join(self.d, "r1"), os.path.join(self.d, "a", "r2")]))

    def test_needs_root(self):
        os.makedirs(os.path.join(self.d, "r1", ".git"))
        code, out = self.run_main("--root", self.d)
        self.assertEqual(code, 2)
        self.assertTrue(out.startswith(f"NEEDS_ROOT: {self.d} is not the top of a git repository."))
        code, out = self.run_main("--root", self.d, "--suggest")
        self.assertEqual(out.splitlines()[1], "Candidates: " + os.path.join(self.d, "r1"))
        empty = os.path.join(self.d, "empty")
        os.makedirs(empty)
        _code, out = self.run_main("--root", empty, "--suggest")
        self.assertIn("Candidates: none found - ask the user where the repo is", out)

    def test_write_and_check(self):
        write(self.d, "src/a.py", "def a(): pass\n")
        code, out = self.run_main("--root", self.d, "--allow-no-git")
        self.assertEqual((code, out), (0, "wrote docs/INDEX.md (9 lines, 1 modules)\n"))
        self.assertTrue(os.path.isfile(os.path.join(self.d, "docs", "INDEX.md")))
        code, out = self.run_main("--root", self.d, "--allow-no-git", "--check")
        self.assertEqual(code, 1)
        self.assertIn("missing docs/ARCHITECTURE.md", out)
        write(self.d, "docs/ARCHITECTURE.md", "a\n")
        write(self.d, "docs/modules/src.md", "a\n")
        self.assertEqual(self.run_main("--root", self.d, "--allow-no-git", "--check"), (0, "docs OK\n"))


if __name__ == "__main__":
    unittest.main()
