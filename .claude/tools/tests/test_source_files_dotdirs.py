"""source_files: dot-folders below the walk root are pruned, except KEEP_DOT_DIRS; the root itself is never pruned."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import ask_code

FILES = {
    "a.py": "x = 1\n",
    "scripts/run.sh": "echo hi\n",
    ".cache/x.py": "x = 2\n",
    ".wt/s/y.py": "y = 1\n",
    ".ruff_cache/z.py": "z = 1\n",
    ".github/c.md": "# ci\n",
    ".notes/n.md": "# note\n",
    ".githooks/helper.py": "h = 1\n",
}


class SourceFilesDotDirs(unittest.TestCase):
    def test_dot_dirs_pruned_except_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            # root lives under a dot-named parent: the root itself must not be skipped
            root = Path(tmp) / ".hidden-parent" / "proj"
            for rel, text in FILES.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            found = set(ask_code.source_files(str(root)))
        self.assertIn("a.py", found)
        self.assertIn(os.path.join("scripts", "run.sh"), found)
        self.assertIn(os.path.join(".github", "c.md"), found)
        self.assertIn(os.path.join(".notes", "n.md"), found)
        self.assertIn(os.path.join(".githooks", "helper.py"), found)
        for gone in (".cache", ".wt", ".ruff_cache"):
            self.assertFalse(any(f.startswith(gone + os.sep) for f in found), gone)

    def test_keep_dot_dirs_constant(self):
        self.assertEqual(ask_code.KEEP_DOT_DIRS, {".github", ".notes", ".githooks"})


if __name__ == "__main__":
    unittest.main()
