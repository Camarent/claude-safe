"""lead_context: the transcript folder follows Claude Code's naming of the real cwd."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import lead_context as lc


class ProjectDirTest(unittest.TestCase):
    def test_host_path(self):
        self.assertEqual(lc.project_dir("/home/user/workspace", home="/home/user"), Path("/home/user/.claude/projects/-home-user-workspace"))

    def test_container_path(self):
        self.assertEqual(lc.project_dir("/workspace", home="/root"), Path("/root/.claude/projects/-workspace"))

    def test_dots_become_dashes(self):
        self.assertEqual(lc.project_dir("/home/u/my.repo", home="/home/u"), Path("/home/u/.claude/projects/-home-u-my-repo"))


if __name__ == "__main__":
    unittest.main()
