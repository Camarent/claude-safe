import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docs_index
from tests.support.tmpdirs import tracked_mkdtemp


def write(root, rel, text="x = 1\n"):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    Path(p).write_text(text, encoding="utf-8")


class IgnoredTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        subprocess.run(["git", "init", "-q", self.d], check=True)
        write(self.d, ".gitignore", "workspace/\n*.secret.py\n")
        write(self.d, "src/a.py", "def pub(): pass\n")
        write(self.d, "src/b.secret.py")
        write(self.d, "src/sub/c.py")
        write(self.d, "workspace/n5/q.py", "def leak(): pass\n")

    def test_ignored_files_and_dirs_absent(self):
        idx = docs_index.build_index(self.d)
        self.assertIn("src/a.py", idx)
        self.assertIn("src/sub/c.py", idx)
        self.assertNotIn("workspace", idx)
        self.assertNotIn("n5", idx)
        self.assertNotIn("secret", idx)

    def test_ignored_module_in_modules_txt(self):
        write(self.d, "docs/modules.txt", "src\nworkspace/n5\n")
        self.assertEqual(docs_index.modules_of(self.d), ["src"])

    def test_tracked_file_stays_even_if_matches_ignore(self):
        subprocess.run(["git", "-C", self.d, "add", "-f", "src/b.secret.py"], check=True)
        self.assertIn("b.secret.py", docs_index.build_index(self.d))

    def test_non_git_dir_lists_everything(self):
        d = tracked_mkdtemp()
        write(d, "src/a.py")
        self.assertIn("src/a.py", docs_index.build_index(d))


if __name__ == "__main__":
    unittest.main()
