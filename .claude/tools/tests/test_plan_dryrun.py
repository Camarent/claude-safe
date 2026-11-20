"""plan.py check: per-file acceptance and Dry run rules; plan.py dryrun on a throwaway git repo."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_dryrun, plan_rules

DECIDE = (
    "Predicted paths: data/readings.json\nTest: python3 -I qrun.py -- node --test t.js\nDone: C1 for each file in data/rare decide keep or drop; C2 checklist\n"
)
TOOL = "Predicted paths: tools/apply-rare-readings.js\nTest: python3 -I qrun.py -- node --test t.js\nDone: C1 run apply-rare-readings.js --dry-run\n"


class Rules(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="synthetic-plan-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def task(self, text):
        with open(os.path.join(self.d, "task-1.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_bare_decide_per_file_rejected(self):
        self.task(DECIDE)
        self.assertIn("no per-file acceptance", plan_rules.per_file_problems(self.d, "task-1")[0])

    def test_outputs_or_test_check_accepts(self):
        self.task(DECIDE + "Outputs: out/a.json, out/b.json\n")
        self.assertEqual(plan_rules.per_file_problems(self.d, "task-1"), [])
        self.task(DECIDE.replace("node --test t.js", "node check.js --fail-on-undecided"))
        self.assertEqual(plan_rules.per_file_problems(self.d, "task-1"), [])

    def test_plain_task_not_per_file(self):
        self.task("Predicted paths: a.py\nTest: pytest\nDone: C1 add helper\n")
        self.assertEqual(plan_rules.per_file_problems(self.d, "task-1"), [])

    def test_tool_needs_dry_run_line(self):
        self.task(TOOL)
        self.assertIn("Dry run", plan_rules.dryrun_missing_problems(self.d, "task-1")[0])
        self.task(TOOL + "Dry run: node tools/apply-rare-readings.js --dry-run  # expect > 0 changes\n")
        self.assertEqual(plan_rules.dryrun_missing_problems(self.d, "task-1"), [])
        self.task("Tool: tools/x.py\nPredicted paths: a.json\n")
        self.assertEqual(len(plan_rules.dryrun_missing_problems(self.d, "task-1")), 1)

    def test_no_dry_run_without_tool(self):
        self.task("Predicted paths: a.py\nDone: C1 x\n")
        self.assertEqual(plan_rules.dryrun_missing_problems(self.d, "task-1"), [])


class Run(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-dryrun-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)
        for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", self.root, *a], check=True, capture_output=True)
        with open(os.path.join(self.root, "f.txt"), "w") as f:
            f.write("x\n")
        for a in (["add", "f.txt"], ["commit", "-qm", "c"]):
            subprocess.run(["git", "-C", self.root, *a], check=True, capture_output=True)
        with open(os.path.join(self.d, "TASKS.md"), "w") as f:
            f.write("## Tasks\n- [ ] task-1 one - owner: haiku - review: wave W1, haiku\n\n## Graph\nwave 1: task-1\n")

    def task(self, line):
        with open(os.path.join(self.d, "task-1.md"), "w") as f:
            f.write(f"Predicted paths: f.txt\n{line}\n")

    def test_noop_fails_and_effect_passes(self):
        self.task("Dry run: echo 0 changes  # expect > 0 changes")
        n, probs = plan_dryrun.dryrun_stage(self.d, self.root)
        self.assertEqual(n, 1)
        self.assertIn("no-op", probs[0])
        self.task("Dry run: echo 3 changes  # expect > 0 changes")
        self.assertEqual(plan_dryrun.dryrun_stage(self.d, self.root), (1, []))

    def test_opt_in_and_cleanup(self):
        self.task("Done: nothing")
        self.assertEqual(plan_dryrun.dryrun_stage(self.d, self.root), (0, []))
        self.task("Dry run: ls  # expect > 0 changes")
        plan_dryrun.dryrun_stage(self.d, self.root)
        wl = subprocess.run(["git", "-C", self.root, "worktree", "list"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(len(wl.strip().splitlines()), 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".work", "s", "wt")))


if __name__ == "__main__":
    unittest.main()
