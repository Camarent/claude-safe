"""worktree._run_setup: the project's `worktree_setup` commands run in a fresh worktree; a failure removes it."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import worktree
from pipeline.configuration import PROJECT_DEFAULTS
from tests.support.tmpdirs import tracked_mkdtemp


class RunSetupTest(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        patch = mock.patch.object(worktree, "remove")
        self.remove = patch.start()
        self.addCleanup(patch.stop)

    def run_setup(self, commands, timeout=30):
        cfg = {"worktree_setup": commands, "worktree_setup_timeout": timeout}
        return worktree._run_setup("/a", "stage", "task-1", self.root, cfg)

    def test_defaults_are_off(self):
        self.assertEqual(PROJECT_DEFAULTS["worktree_setup"], [])
        self.assertGreater(PROJECT_DEFAULTS["worktree_setup_timeout"], 0)

    def test_config_without_the_keys_is_fine(self):
        worktree._run_setup("/a", "stage", "task-1", self.root, {})
        worktree._run_setup("/a", "stage", "task-1", self.root, {"worktree_setup": ["echo ok > ok.txt"]})
        self.assertTrue(Path(self.root, "ok.txt").exists())

    def test_no_commands_does_nothing(self):
        self.run_setup([])
        self.remove.assert_not_called()

    def test_commands_run_in_order_in_the_worktree(self):
        self.run_setup(["echo one > out.txt", "echo two >> out.txt", "pwd > cwd.txt"])
        self.assertEqual(Path(self.root, "out.txt").read_text(encoding="utf-8"), "one\ntwo\n")
        self.assertEqual(os.path.realpath(Path(self.root, "cwd.txt").read_text(encoding="utf-8").strip()), os.path.realpath(self.root))
        self.remove.assert_not_called()

    def test_failure_removes_the_worktree_and_names_the_command(self):
        with self.assertRaises(RuntimeError) as cm:
            self.run_setup(["echo ready > a.txt", "echo broken >&2; exit 3", "echo never > b.txt"])
        msg = str(cm.exception)
        self.assertIn("exit 3", msg)
        self.assertIn("broken", msg)
        self.assertFalse(Path(self.root, "b.txt").exists())
        self.remove.assert_called_once_with("/a", "stage", "task-1", force=True)

    def test_timeout_removes_the_worktree(self):
        with self.assertRaises(RuntimeError) as cm:
            self.run_setup(["exec sleep 5"], timeout=0.2)  # exec: the shell is replaced, so the kill ends the sleep too
        self.assertIn("timed out", str(cm.exception))
        self.remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
