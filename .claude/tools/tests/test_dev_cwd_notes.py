"""dev-cwd constants: the guard env names and the MAIN-DIRT notes say the job worktree root is the dev's current directory."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.configuration import (
    ENV_GUARD_ALLOW,
    ENV_GUARD_JOB,
    ENV_GUARD_LOG,
    ENV_GUARD_MAIN,
    ENV_GUARD_WORKTREE,
    GUARD_LOG_FILE,
    GUARD_TOOLS,
    MAIN_DIRT_REWORK_NOTE,
    MAIN_WRITE_NOTE,
    WORKTREE_CWD_AGENTS,
)


class GuardConstants(unittest.TestCase):
    def test_env_names_are_distinct_strings(self):
        names = [ENV_GUARD_WORKTREE, ENV_GUARD_MAIN, ENV_GUARD_ALLOW, ENV_GUARD_JOB, ENV_GUARD_LOG]
        self.assertEqual(len(set(names)), len(names))
        self.assertTrue(all(n.startswith("PIPELINE_GUARD_") for n in names))

    def test_log_file_and_tools(self):
        self.assertEqual(GUARD_LOG_FILE, "write-guard.jsonl")
        self.assertEqual(GUARD_TOOLS, ("Write", "Edit", "MultiEdit", "NotebookEdit"))

    def test_only_dev_roles_get_worktree_cwd(self):
        self.assertEqual(WORKTREE_CWD_AGENTS, ("dev", "pl-dev"))


class NoteWording(unittest.TestCase):
    def test_write_note_names_current_directory(self):
        note = MAIN_WRITE_NOTE.format(files="f (mtime t, job start t)", more=0)
        self.assertIn("current directory", note)
        self.assertNotIn("relative paths only", note)

    def test_rework_note_names_current_directory(self):
        self.assertIn("current directory", MAIN_DIRT_REWORK_NOTE)
        self.assertNotIn("relative paths only", MAIN_DIRT_REWORK_NOTE)


if __name__ == "__main__":
    unittest.main()
