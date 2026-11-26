"""docs_index: --fresh staleness check, module_headings, --docs-dir option and the fold hint."""

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
        # the temp dir can sit under an ignored .work/ of the enclosing repo; these tests do not test git ignore rules
        ignore = mock.patch.object(D, "ignored_paths", return_value=set())
        ignore.start()
        self.addCleanup(ignore.stop)

    def run_main(self, *args):
        buf = io.StringIO()
        code: str | int | None = 0
        with mock.patch.object(sys, "argv", ["docs_index", *args]), contextlib.redirect_stdout(buf):
            try:
                D.main()
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()


class Fresh(Tmp):
    def setUp(self):
        super().setUp()
        write(self.d, "src/a.py", "def a(): pass\n")
        self.assertEqual(self.run_main("--root", self.d, "--allow-no-git")[0], 0)

    def test_fresh_after_write_exits_zero(self):
        self.assertEqual(self.run_main("--root", self.d, "--allow-no-git", "--fresh"), (0, "docs/INDEX.md is fresh\n"))

    def test_module_doc_edit_alone_keeps_fresh(self):
        # decoy: only INDEX.md content decides freshness, not the other docs
        write(self.d, "docs/modules/src.md", "edited after indexing\n")
        self.assertEqual(self.run_main("--root", self.d, "--allow-no-git", "--fresh")[0], 0)

    def test_source_change_makes_stale(self):
        write(self.d, "src/a.py", "def a(): pass\ndef b(): pass\n")
        code, out = self.run_main("--root", self.d, "--allow-no-git", "--fresh")
        self.assertEqual(code, 1)
        self.assertIn("docs/INDEX.md is stale", out)

    def test_missing_index_is_stale(self):
        os.remove(os.path.join(self.d, "docs", "INDEX.md"))
        code, out = self.run_main("--root", self.d, "--allow-no-git", "--fresh")
        self.assertEqual(code, 1)
        self.assertIn("docs/INDEX.md is stale", out)

    def test_fresh_writes_nothing(self):
        write(self.d, "docs/INDEX.md", "hand edited\n")
        self.assertEqual(self.run_main("--root", self.d, "--allow-no-git", "--fresh")[0], 1)
        self.assertEqual(Path(self.d, "docs", "INDEX.md").read_text(encoding="utf-8"), "hand edited\n")


class Headings(Tmp):
    def test_module_headings_match_modules_of(self):
        write(self.d, "docs/modules.txt", "# listed\nbeta/\nalpha\n")
        self.assertEqual(D.module_headings(self.d), ["alpha", "beta"])
        self.assertEqual(D.module_headings(self.d), D.modules_of(self.d))


class DocsDir(Tmp):
    def test_docs_dir_option_replaces_docs_folder(self):
        write(self.d, "src/a.py", "def a(): pass\n")
        write(self.d, "documentation/modules.txt", "src\n")
        self.assertEqual(
            self.run_main("--root", self.d, "--allow-no-git", "--docs-dir", "documentation"),
            (0, "wrote documentation/INDEX.md (9 lines, 1 modules)\n"),
        )
        self.assertTrue(os.path.isfile(os.path.join(self.d, "documentation", "INDEX.md")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "docs")))
        self.assertEqual(self.run_main("--root", self.d, "--allow-no-git", "--docs-dir", "documentation", "--fresh")[0], 0)
        code, out = self.run_main("--root", self.d, "--allow-no-git", "--docs-dir", "documentation", "--check")
        self.assertEqual(code, 1)
        self.assertIn("missing documentation/ARCHITECTURE.md", out)


class FoldHint(Tmp):
    def test_module_doc_without_module_gets_fold_hint(self):
        write(self.d, "src/a.py", "def a(): pass\n")
        write(self.d, "docs/ARCHITECTURE.md", "arch\n")
        write(self.d, "docs/modules/src.md", "fine\n")
        write(self.d, "docs/modules/core--mcp.md", "orphan\n")
        self.assertEqual(
            D.check(self.d),
            ["docs/modules/core--mcp.md describes no existing module - fold it into its parent module doc"],
        )


if __name__ == "__main__":
    unittest.main()
