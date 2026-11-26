"""scripts/sync-claude-config.sh: refuses during a driver step, runs claude-config from a detached worktree; dry run by default, --apply writes.

install-claude-config.sh is stubbed here. The real install is covered in test_install_claude_config.py: dry run lists
would-overwrite names and leaves HOME byte-identical, and a real run keeps the old copy under .provision-backup.
"""

import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "sync-claude-config.sh"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
# fakes log their arguments to $SYNC_TEST_LOG (a file outside the worktree, which the script removes)
FAKE_INSTALL = 'echo "install $*" >>"$SYNC_TEST_LOG"\n'
FAKE_HOST_AGENTS = 'import os, sys\nopen(os.environ["SYNC_TEST_LOG"], "a").write("host_agents " + " ".join(sys.argv[1:]) + "\\n")\n'


class SyncScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.log = self.tmp / "calls.log"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "scripts" / "sync-claude-config.sh")
        (self.repo / "scripts" / "install-claude-config.sh").write_text(FAKE_INSTALL)
        (self.repo / "scripts" / "host_agents.py").write_text(FAKE_HOST_AGENTS)
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "fakes")
        self.env = {**os.environ, **GIT_ENV, "SYNC_TEST_LOG": str(self.log)}

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV}
        )

    def _run(self, *args):
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "sync-claude-config.sh"), *args], check=False, capture_output=True, text=True, env=self.env, timeout=60
        )

    def _calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_default_run_is_dry_run_and_writes_nothing(self):
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._calls(), ["install --dry-run", "host_agents "])  # install gets --dry-run, host_agents no args
        self.assertIn("nothing written", res.stdout)
        self.assertIn("rerun with --apply to write", res.stdout)
        self.assertNotIn("restart Claude Code", res.stdout)
        worktrees = self._git("worktree", "list", "--porcelain").stdout
        self.assertEqual(worktrees.count("worktree "), 1, worktrees)  # the temp worktree is removed

    def test_apply_writes_through_install_without_dry_run(self):
        res = self._run("--apply")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._calls(), ["install ", "host_agents "])  # install writes: no --dry-run, no --apply
        self.assertIn("restart Claude Code", res.stdout)
        self.assertIn("synced ~/.claude from commit", res.stdout)
        worktrees = self._git("worktree", "list", "--porcelain").stdout
        self.assertEqual(worktrees.count("worktree "), 1, worktrees)  # the temp worktree is removed

    def test_usage_and_unknown_flag(self):
        res = self._run("--help")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("usage: scripts/sync-claude-config.sh [--apply] [REF]", res.stdout)
        self.assertIn("dry run", res.stdout)
        res = self._run("--bogus")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self._calls(), [])

    def test_refuses_while_driver_holds_stage_lock(self):
        lock_dir = self.repo / ".work" / "pipeline-hardening"
        lock_dir.mkdir(parents=True)
        fd = os.open(lock_dir / "wave.lock", os.O_RDWR | os.O_CREAT)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)  # a second open file description: the script's `flock -n` fails
        res = self._run()
        self.assertEqual(res.returncode, 1)
        self.assertIn("mid-step", res.stdout)
        self.assertEqual(self._calls(), [])

    def test_script_never_passes_apply_to_host_agents(self):
        # join backslash continuations so a multi-line host_agents.py call is checked as one statement
        text = SCRIPT.read_text().replace("\\\n", " ")
        calls = [ln for ln in text.splitlines() if "host_agents.py" in ln]
        self.assertTrue(calls, "host_agents.py call not found")
        self.assertEqual([ln for ln in calls if "--apply" in ln], [])


if __name__ == "__main__":
    unittest.main()
