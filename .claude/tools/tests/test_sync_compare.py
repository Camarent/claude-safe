"""scripts/sync-claude-config.sh --compare against a real install: --apply then --compare exits 0; a real difference exits 10.

Unlike test_sync_script.py (fake install), this runs the real scripts/install-claude-config.sh and host_agents.py in a
temp repo copy with a temp HOME. Both are removed by addCleanup.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".work", "logs", "parked")


class SyncCompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.repo / "scripts").mkdir(parents=True)
        for name in ("sync-claude-config.sh", "install-claude-config.sh", "host_agents.py"):
            shutil.copy(REPO / "scripts" / name, self.repo / "scripts" / name)
        shutil.copytree(REPO / ".claude", self.repo / ".claude", ignore=COPY_IGNORE)
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "copy of the pipeline")
        self.env = {**os.environ, **GIT_ENV, "HOME": str(self.home)}

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV}
        )

    def _run(self, *args):
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "sync-claude-config.sh"), *args], check=False, capture_output=True, text=True, env=self.env, timeout=300
        )

    def _apply(self):
        res = self._run("--apply")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_apply_then_compare_exits_0_and_lists_no_agent(self):
        self._apply()
        res = self._run("--compare")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("matches commit", res.stdout)
        self.assertNotIn("agents/", res.stdout)

    def test_changed_tool_file_in_home_exits_10(self):
        self._apply()
        tool = self.home / ".claude" / "tools" / "pipeline" / "cmdindex.py"
        tool.write_text(tool.read_text() + "\n# edited on the host\n")
        res = self._run("--compare")
        self.assertEqual(res.returncode, 10, res.stdout + res.stderr)
        self.assertIn("tools/pipeline/cmdindex.py", res.stdout)

    def test_changed_agent_body_in_home_exits_10(self):
        self._apply()
        agent = self.home / ".claude" / "agents" / "dev.md"
        agent.write_text(agent.read_text() + "\nextra line\n")
        res = self._run("--compare")
        self.assertEqual(res.returncode, 10, res.stdout + res.stderr)
        self.assertIn("agents/dev.md", res.stdout)
