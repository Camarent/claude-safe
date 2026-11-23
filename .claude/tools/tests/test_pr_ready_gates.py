"""pr-ready runs every project gate and the uncovered CI run lines before the push; a red one blocks and is named."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_ops
from pipeline.configuration.pr_gates import PR_GATE_TIMEOUT_S
from tests.support.gitrepo import git

CI_YML = """\
name: ci
jobs:
  test:
    steps:
      - run: scripts/dev.sh check
      - run: scripts/dev.sh extra-ci
      - run: |
          scripts/dev.sh sec
          echo done
"""

CFG = {
    "gates": [{"name": "check", "cmd": "scripts/dev.sh check"}, {"name": "ccn", "cmd": "scripts/dev.sh ccn"}],
    "gates_merge": [{"name": "test", "cmd": "scripts/dev.sh test"}],
    "gates_final": [{"name": "sec", "cmd": "scripts/dev.sh sec"}],
}


class FakeRunner:
    """Records each command; returns `red` as its exit code, 0 otherwise, and `out[cmd]` as its stderr."""

    def __init__(self, red=(), out=None):
        self.red, self.out, self.seen = set(red), out or {}, []

    def __call__(self, args, cwd, timeout):
        self.seen.append((args[-1], cwd, timeout))
        return SimpleNamespace(returncode=1 if args[-1] in self.red else 0, stdout="", stderr=self.out.get(args[-1], ""))


class PrReadyGatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        wf = os.path.join(self.tmp.name, ".github", "workflows")
        os.makedirs(wf)
        with open(os.path.join(wf, "ci.yml"), "w", encoding="utf-8") as fh:
            fh.write(CI_YML)
        git(self.tmp.name, "init", "-q")

    def test_every_gate_and_uncovered_ci_line_runs_in_repo(self):
        fake = FakeRunner()
        note = pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=fake)
        self.assertEqual(
            [c for c, _d, _t in fake.seen],
            ["scripts/dev.sh check", "scripts/dev.sh ccn", "scripts/dev.sh test", "scripts/dev.sh sec", "scripts/dev.sh extra-ci"],
        )
        self.assertTrue(all(d == self.tmp.name and t == PR_GATE_TIMEOUT_S for _c, d, t in fake.seen))
        self.assertIn("5 command(s) green", note)

    def test_red_gate_blocks_and_names_it(self):
        with self.assertRaises(pr_ops.Blocked) as cm:
            pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=FakeRunner(red={"scripts/dev.sh ccn"}))
        self.assertIn("`scripts/dev.sh ccn`", str(cm.exception))
        self.assertIn("pr-ready gates red on stage/x", str(cm.exception))

    def test_ci_ignore_skips_ci_only_line(self):
        fake = FakeRunner()
        cfg = {**CFG, "ci_drift_ignore": ["scripts/dev.sh extra-ci"]}
        pr_ops._gates_gate(self.tmp.name, cfg, "stage/x", runner=fake)
        self.assertNotIn("scripts/dev.sh extra-ci", [c for c, _d, _t in fake.seen])

    def test_red_ci_only_line_gets_ignore_hint(self):
        with self.assertRaises(pr_ops.Blocked) as cm:
            pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=FakeRunner(red={"scripts/dev.sh extra-ci"}))
        self.assertIn('`scripts/dev.sh extra-ci` -> add `ci_drift_ignore` entry "scripts/dev.sh extra-ci"', str(cm.exception))
        self.assertNotIn("environment, not code", str(cm.exception))

    def test_red_ci_only_line_with_config_error_is_environment(self):
        runner = FakeRunner(red={"scripts/dev.sh extra-ci"}, out={"scripts/dev.sh extra-ci": "ConfigError: no config path"})
        with self.assertRaises(pr_ops.Blocked) as cm:
            pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=runner)
        self.assertIn("`scripts/dev.sh extra-ci` -> environment, not code: set the variable or add the ignore entry", str(cm.exception))
        self.assertNotIn("ci_drift_ignore", str(cm.exception))

    def test_red_ci_only_line_with_unset_variable_is_environment(self):
        runner = FakeRunner(red={"scripts/dev.sh extra-ci"}, out={"scripts/dev.sh extra-ci": "KATA_TOKEN is not set"})
        with self.assertRaises(pr_ops.Blocked) as cm:
            pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=runner)
        self.assertIn("environment, not code", str(cm.exception))

    def test_ordinary_assertion_in_ci_line_gets_hint_not_environment(self):
        runner = FakeRunner(red={"scripts/dev.sh extra-ci"}, out={"scripts/dev.sh extra-ci": "AssertionError: 3 != 4"})
        with self.assertRaises(pr_ops.Blocked) as cm:
            pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=runner)
        self.assertIn("ci_drift_ignore", str(cm.exception))
        self.assertNotIn("environment, not code", str(cm.exception))

    def test_red_gate_keeps_plain_text_even_with_config_error(self):
        runner = FakeRunner(red={"scripts/dev.sh ccn"}, out={"scripts/dev.sh ccn": "ConfigError: no config path"})
        with self.assertRaises(pr_ops.Blocked) as cm:
            pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=runner)
        msg = str(cm.exception)
        self.assertIn("pr-ready gates red on stage/x: `scripts/dev.sh ccn`;", msg)
        self.assertNotIn("ci_drift_ignore", msg)
        self.assertNotIn("environment, not code", msg)

    def test_no_gates_and_no_workflows_runs_nothing(self):
        fake = FakeRunner()
        with tempfile.TemporaryDirectory() as empty:
            git(empty, "init", "-q")
            note = pr_ops._gates_gate(empty, {}, "stage/x", runner=fake)
        self.assertEqual(fake.seen, [])
        self.assertIn("0 command(s) green", note)

    def _ci_file_runner(self, red=()):
        """FakeRunner that makes the CI line `scripts/dev.sh extra-ci` write requirements-ci.txt (the stray file of kata)."""
        inner = FakeRunner(red=red)

        def runner(args, cwd, timeout):
            if args[-1] == "scripts/dev.sh extra-ci":
                with open(os.path.join(cwd, "requirements-ci.txt"), "w", encoding="utf-8") as fh:
                    fh.write("stray\n")
            return inner(args, cwd, timeout)

        return runner

    def test_files_ci_line_creates_are_removed_and_old_untracked_kept(self):
        with open(os.path.join(self.tmp.name, "keep.txt"), "w", encoding="utf-8") as fh:
            fh.write("mine\n")
        note = pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=self._ci_file_runner())
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "requirements-ci.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "keep.txt")))
        self.assertIn("5 command(s) green", note)

    def test_red_result_is_kept_and_ci_files_are_still_removed(self):
        runner = self._ci_file_runner(red={"scripts/dev.sh extra-ci"})
        with self.assertRaises(pr_ops.Blocked) as cm:
            pr_ops._gates_gate(self.tmp.name, CFG, "stage/x", runner=runner)
        self.assertIn("ci_drift_ignore", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "requirements-ci.txt")))


if __name__ == "__main__":
    unittest.main()
