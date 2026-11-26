import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import tracked_mkdtemp

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "install-claude-config.sh"


def snapshot(root):
    """Relative path -> bytes for every file under root (a directory tree as one comparable value)."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@unittest.skipIf(os.geteuid() == 0, "script refuses root")
@unittest.skipUnless(all(shutil.which(t) for t in ("rsync", "jq", "sudo")), "needs rsync, jq and sudo")
class InstallClaudeConfigTest(unittest.TestCase):
    """The claude-config install against a scratch checkout and a scratch HOME: reruns carry updates over."""

    def setUp(self):
        root = Path(tracked_mkdtemp())
        self.src = root / "checkout" / ".claude"
        self.home = root / "home"
        (self.src / "tools").mkdir(parents=True)
        (self.src / "bin").mkdir()
        (self.src / "tools" / "a.py").write_text("v1\n", encoding="utf-8")
        (self.src / "bin" / "cs-x").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.src / "settings.json").write_text('{"hooks": {"PreToolUse": []}}', encoding="utf-8")
        (root / "checkout" / "scripts").mkdir()
        self.script = root / "checkout" / "scripts" / "install-claude-config.sh"
        shutil.copy(SCRIPT, self.script)
        self.home.mkdir()
        self.dst = self.home / ".claude"

    def sync(self):
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(self.script)], capture_output=True, text=True, check=False, timeout=60, env=env)

    def test_rerun_without_changes_is_skipped(self):
        self.assertIn("done claude-config", self.sync().stdout)
        self.assertIn("skip claude-config: already done", self.sync().stdout)

    def test_updated_file_reaches_the_vm_and_old_copy_is_kept(self):
        self.sync()
        (self.src / "tools" / "a.py").write_text("v2 longer\n", encoding="utf-8")
        r = self.sync()
        self.assertIn("done claude-config", r.stdout)
        self.assertEqual((self.dst / "tools" / "a.py").read_text(encoding="utf-8"), "v2 longer\n")
        saved = list((self.dst / ".provision-backup").glob("*/tools/a.py"))
        self.assertEqual([p.read_text(encoding="utf-8") for p in saved], ["v1\n"])
        self.assertIn("skip claude-config: already done", self.sync().stdout)

    def test_new_file_in_the_checkout_is_copied(self):
        self.sync()
        (self.src / "tools" / "b.py").write_text("new\n", encoding="utf-8")
        self.assertIn("done claude-config", self.sync().stdout)
        self.assertEqual((self.dst / "tools" / "b.py").read_text(encoding="utf-8"), "new\n")

    def test_local_edit_of_a_managed_file_is_replaced_but_saved(self):
        self.sync()
        (self.dst / "tools" / "a.py").write_text("hand edit\n", encoding="utf-8")
        self.assertIn("done claude-config", self.sync().stdout)
        self.assertEqual((self.dst / "tools" / "a.py").read_text(encoding="utf-8"), "v1\n")
        saved = list((self.dst / ".provision-backup").glob("*/tools/a.py"))
        self.assertEqual([p.read_text(encoding="utf-8") for p in saved], ["hand edit\n"])

    def test_dry_run_lists_overwrites_and_leaves_home_byte_identical(self):
        self.sync()
        (self.src / "tools" / "a.py").write_text("v2 longer\n", encoding="utf-8")
        (self.src / "tools" / "b.py").write_text("new\n", encoding="utf-8")
        before = snapshot(self.dst)
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(self.script), "--dry-run"], capture_output=True, text=True, check=False, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        listed = "\n" + r.stdout + "\n"
        self.assertIn("\n  tools/a.py\n", listed)
        self.assertIn("\n  tools/b.py\n", listed)
        self.assertNotIn("tools/tools/", r.stdout)
        self.assertIn("not listed here", r.stdout)  # settings.json, ~/.local/bin, ~/.profile are named as unlisted
        self.assertEqual(snapshot(self.dst), before)
        self.assertFalse((self.dst / ".provision-backup").exists())

    def test_dry_run_dies_when_rsync_fails_instead_of_saying_up_to_date(self):
        fake = self.home.parent / "fakebin"  # a rsync that always fails, first on PATH
        fake.mkdir()
        (fake / "rsync").write_text("#!/bin/sh\necho 'rsync: boom' >&2\nexit 23\n", encoding="utf-8")
        (fake / "rsync").chmod(0o755)
        env = {**os.environ, "HOME": str(self.home), "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
        r = subprocess.run(["bash", str(self.script), "--dry-run"], capture_output=True, text=True, check=False, timeout=60, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("rsync dry run failed", r.stderr)
        self.assertNotIn("up to date", r.stdout)
        self.assertFalse(self.dst.exists())

    def test_dry_run_with_a_missing_local_bin_link_does_not_claim_up_to_date(self):
        self.sync()  # ~/.claude is complete; removing one ~/.local/bin link makes the install step due without any file drift
        (self.home / ".local" / "bin" / "cs-x").unlink()
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(self.script), "--dry-run"], capture_output=True, text=True, check=False, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("none: no file would be overwritten; the changes below may still apply", r.stdout)
        self.assertNotIn("up to date", r.stdout)

    def test_failing_rsync_on_a_populated_home_is_not_reported_as_done(self):
        self.sync()  # ~/.claude and the ~/.local/bin links are now complete: only the drift check can notice rsync
        fake = self.home.parent / "fakebin"
        fake.mkdir()
        (fake / "rsync").write_text("#!/bin/sh\necho 'rsync: boom' >&2\nexit 23\n", encoding="utf-8")
        (fake / "rsync").chmod(0o755)
        env = {**os.environ, "HOME": str(self.home), "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
        r = subprocess.run(["bash", str(self.script)], capture_output=True, text=True, check=False, timeout=60, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("rsync dry run failed", r.stderr)
        self.assertNotIn("already done", r.stdout)

    def test_vm_only_files_stay_and_pycache_is_ignored(self):
        self.sync()
        (self.dst / "tools" / "local.py").write_text("mine\n", encoding="utf-8")
        (self.dst / "skills").mkdir()
        (self.dst / "skills" / "own.md").write_text("own\n", encoding="utf-8")
        cache = self.dst / "tools" / "__pycache__"
        cache.mkdir()
        (cache / "a.cpython-314.pyc").write_bytes(b"x")
        self.assertIn("skip claude-config: already done", self.sync().stdout)
        self.assertTrue((self.dst / "tools" / "local.py").exists())
        self.assertTrue((self.dst / "skills" / "own.md").exists())


if __name__ == "__main__":
    unittest.main()
