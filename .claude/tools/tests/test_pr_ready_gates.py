"""pr-ready runs every project gate and the uncovered CI run lines before the push; a red one blocks and is named."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_ops
from pipeline.configuration.pr_gates import PR_GATE_TIMEOUT_S

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
    """Records each command; returns `red` as its exit code, 0 otherwise."""

    def __init__(self, red=()):
        self.red, self.seen = set(red), []

    def __call__(self, args, cwd, timeout):
        self.seen.append((args[-1], cwd, timeout))
        return SimpleNamespace(returncode=1 if args[-1] in self.red else 0)


class PrReadyGatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        wf = os.path.join(self.tmp.name, ".github", "workflows")
        os.makedirs(wf)
        with open(os.path.join(wf, "ci.yml"), "w", encoding="utf-8") as fh:
            fh.write(CI_YML)

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

    def test_no_gates_and_no_workflows_runs_nothing(self):
        fake = FakeRunner()
        with tempfile.TemporaryDirectory() as empty:
            note = pr_ops._gates_gate(empty, {}, "stage/x", runner=fake)
        self.assertEqual(fake.seen, [])
        self.assertIn("0 command(s) green", note)


if __name__ == "__main__":
    unittest.main()
