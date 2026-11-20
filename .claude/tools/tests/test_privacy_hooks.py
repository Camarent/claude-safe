"""git/hooks pre-commit + commit-msg read privacy.blocklist from git config; no config -> notice and pass."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "git" / "hooks"
NOTICE = "no privacy.blocklist configured, check skipped"


@unittest.skipUnless(shutil.which("sh") and shutil.which("git"), "needs sh and git")
class PrivacyHooks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.git("init", "-q")
        self.git("config", "core.hooksPath", str(HOOKS))

    def git(self, *args):
        cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args]
        return subprocess.run(cmd, cwd=self.repo, check=False, capture_output=True, text=True)

    def commit(self, text, msg="clean"):
        (self.repo / "f.txt").write_text(text + "\n")
        self.git("add", "f.txt")
        return self.git("commit", "-m", msg)

    def test_staged_line_rejected(self):
        self.git("config", "privacy.blocklist", "secret-word")
        r = self.commit("has Secret-Word inside")
        self.assertNotEqual(r.returncode, 0)

    def test_message_rejected(self):
        self.git("config", "privacy.blocklist", "secret-word")
        r = self.commit("fine", msg="mentions secret-word")
        self.assertNotEqual(r.returncode, 0)

    def test_clean_commit_passes(self):
        self.git("config", "privacy.blocklist", "secret-word")
        self.assertEqual(self.commit("fine").returncode, 0)

    def test_no_config_passes_with_notice(self):
        r = self.commit("secret-word")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(NOTICE, r.stderr)


if __name__ == "__main__":
    unittest.main()
