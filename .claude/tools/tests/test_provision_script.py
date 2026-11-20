import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import tracked_mkdtemp

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "provision-dev-vm.sh"
STEPS = [
    "base",
    "gh",
    "chrome",
    "node",
    "uv",
    "pytools",
    "jstools",
    "gitleaks",
    "snip",
    "cdt-mcp",
    "docker",
    "cpp",
    "rust",
    "office",
    "audio",
    "postgres",
    "claude",
    "claude-config",
    "config",
]


def run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False, timeout=60)


@unittest.skipIf(os.geteuid() == 0, "script refuses root")
class ProvisionScriptTest(unittest.TestCase):
    def test_help(self):
        r = run("-h")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--dry-run", r.stdout)

    def test_list_names_every_step(self):
        r = run("--list")
        self.assertEqual(r.returncode, 0)
        names = [line.split()[0] for line in r.stdout.splitlines()]
        self.assertEqual(names, STEPS)

    def test_dry_run_prints_each_step(self):
        r = run("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        for s in STEPS:
            self.assertRegex(r.stdout, rf"(would run|skip) {re.escape(s)}\b")

    def test_unknown_step_rejected(self):
        for opt in ("--only", "--skip"):
            r = run(opt, "base,nope")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("nope", r.stderr)

    def test_first_step_name_always_accepted(self):
        # grep -q on a pipe raced with SIGPIPE under pipefail and rejected valid names under load.
        # The bad second name makes the script exit right after validation: ~25 ms a run.
        for _ in range(40):
            r = run("--skip", "base,nope")
            self.assertIn("'nope'", r.stderr)
            self.assertNotIn("'base'", r.stderr)

    def test_unknown_argument_rejected(self):
        self.assertNotEqual(run("--bogus").returncode, 0)

    def test_pins_match_dockerfiles(self):
        text = "\n".join(p.read_text() for p in [REPO / "Dockerfile", *(REPO / "profiles").glob("*.Dockerfile")])
        pins = dict(re.findall(r"^ARG (\w+_VERSION)=(\S+)$", text, re.M))
        script = SCRIPT.read_text()
        seen = 0
        for name, value in pins.items():
            m = re.search(rf"^{name}=(\S+)$", script, re.M)
            if m:
                seen += 1
                self.assertEqual(m.group(1), value, name)
        self.assertGreater(seen, 20)


@unittest.skipIf(os.geteuid() == 0, "script refuses root")
@unittest.skipUnless(all(shutil.which(t) for t in ("rsync", "jq", "sudo")), "needs rsync, jq and sudo")
class ClaudeConfigSyncTest(unittest.TestCase):
    """The claude-config step against a scratch checkout and a scratch HOME: reruns carry updates over."""

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
        self.script = root / "checkout" / "scripts" / "provision-dev-vm.sh"
        shutil.copy(SCRIPT, self.script)
        self.home.mkdir()
        self.dst = self.home / ".claude"

    def sync(self):
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(self.script), "--only", "claude-config"], capture_output=True, text=True, check=False, timeout=60, env=env)

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
