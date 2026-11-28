"""worktree._run_setup: the project's `worktree_setup` commands run in a fresh worktree; a failure removes it."""

import os
import subprocess
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

    def test_new_defaults_are_empty(self):
        self.assertEqual(PROJECT_DEFAULTS["worktree_requirements"], [])
        self.assertEqual(PROJECT_DEFAULTS["test_roots"], [])
        self.assertEqual(PROJECT_DEFAULTS["test_wrap"], [])
        self.assertEqual(PROJECT_DEFAULTS["roadmap_file"], "")


class RequirementsInstallTest(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        self.a_dir = tracked_mkdtemp()
        patch = mock.patch.object(worktree, "remove")
        self.remove = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(worktree.util, "append_jsonl")
        self.events = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(worktree.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.fake_run = patch.start()
        self.addCleanup(patch.stop)

    def setup_with(self, cfg):
        worktree._run_setup(self.a_dir, "stage", "task-1", self.root, cfg)

    def pip_calls(self):
        return [c.args[0] for c in self.fake_run.call_args_list if c.args[0][1:3] == ["-m", "pip"]]

    def test_listed_requirements_are_installed_with_the_venv_python(self):
        req = Path(self.root, "requirements.txt")
        req.write_text("", encoding="utf-8")
        self.setup_with({"worktree_requirements": ["requirements*.txt"]})
        venv_python = os.path.join(self.root, ".venv", "bin", "python")
        self.assertEqual(self.pip_calls(), [[venv_python, "-m", "pip", "install", "-r", str(req)]])
        self.assertEqual(self.fake_run.call_args.kwargs["cwd"], self.root)
        self.remove.assert_not_called()

    def test_each_install_logs_one_setup_event(self):
        Path(self.root, "requirements.txt").write_text("", encoding="utf-8")
        Path(self.root, "dev.txt").write_text("", encoding="utf-8")
        self.setup_with({"worktree_setup": ["true"], "worktree_requirements": ["*.txt"]})
        evs = [c.args[1]["ev"] for c in self.events.call_args_list]
        self.assertEqual(evs, [worktree.EV_SETUP] * 3)  # one command + two requirements files

    def test_missing_requirements_file_is_skipped(self):
        self.setup_with({"worktree_requirements": ["requirements*.txt"]})
        self.assertEqual(self.pip_calls(), [])
        self.remove.assert_not_called()

    def test_failing_install_removes_the_worktree(self):
        Path(self.root, "requirements.txt").write_text("", encoding="utf-8")
        self.fake_run.return_value = subprocess.CompletedProcess([], 1, "", "no index")
        with self.assertRaises(RuntimeError) as cm:
            self.setup_with({"worktree_requirements": ["requirements*.txt"]})
        self.assertIn("pip install -r requirements.txt", str(cm.exception))
        self.remove.assert_called_once_with(self.a_dir, "stage", "task-1", force=True)

    def test_configured_venv_python_is_used(self):
        Path(self.root, "requirements.txt").write_text("", encoding="utf-8")
        self.setup_with({"worktree_requirements": ["requirements*.txt"], "worktree_venv_python": "env/bin/python"})
        self.assertEqual(self.pip_calls()[0][0], os.path.join(self.root, "env", "bin", "python"))

    def test_missing_venv_python_removes_the_worktree(self):
        Path(self.root, "requirements.txt").write_text("", encoding="utf-8")
        self.fake_run.side_effect = FileNotFoundError(2, "No such file or directory")
        with self.assertRaises(RuntimeError) as cm:
            self.setup_with({"worktree_requirements": ["requirements*.txt"]})
        self.assertIn("could not run", str(cm.exception))
        self.remove.assert_called_once_with(self.a_dir, "stage", "task-1", force=True)


if __name__ == "__main__":
    unittest.main()
