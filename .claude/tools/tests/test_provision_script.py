import os
import re
import subprocess
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
