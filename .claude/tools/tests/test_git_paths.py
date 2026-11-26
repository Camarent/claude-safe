import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testrun
from pipeline import gitx
from retro import incidents

NAMES = ("with space.py", "café.py")


class GitPathTest(unittest.TestCase):
    def test_unquote_octal(self):
        self.assertEqual(gitx.unquote_path('"caf\\303\\251 x.py"'), "café x.py")
        self.assertEqual(gitx.unquote_path('"a\\tb\\"c"'), 'a\tb"c')

    def test_added_lines_quoted_non_ascii(self):
        diff = '--- /dev/null\n+++ "b/caf\\303\\251.py"\n@@ -0,0 +1 @@\n+x\n'
        self.assertEqual(gitx.added_lines(diff), {"café.py": [(1, "x")]})

    def test_patch_files(self):
        text = 'diff --git a/with space.py b/with space.py\n--- a\ndiff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        self.assertEqual(incidents._patch_files(text), list(NAMES))

    def test_imported_by_changes(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            for n in NAMES:
                with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                    f.write("import foo\n")
            self.assertTrue(testrun._imported_by_changes(d, "foo"))
            os.remove(os.path.join(d, NAMES[0]))
            self.assertTrue(testrun._imported_by_changes(d, "foo"))
            os.remove(os.path.join(d, NAMES[1]))
            self.assertFalse(testrun._imported_by_changes(d, "foo"))


if __name__ == "__main__":
    unittest.main()
