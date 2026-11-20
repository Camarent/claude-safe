import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testrun


class UntrackedDirTest(unittest.TestCase):
    def test_import_in_new_untracked_dir_is_seen(self):
        # without -uall git lists the new dir as "?? pkg/" and the file inside is never opened
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            os.makedirs(os.path.join(d, "pkg"))
            with open(os.path.join(d, "pkg", "m.py"), "w", encoding="utf-8") as f:
                f.write("import foo_missing\n")
            self.assertTrue(testrun._imported_by_changes(d, "foo_missing"))


if __name__ == "__main__":
    unittest.main()
