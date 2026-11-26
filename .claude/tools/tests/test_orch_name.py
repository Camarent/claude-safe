"""orch retry/skip take NAME (the feature): the step lands in the feature's first open phase; the old --stage and phase-path forms still work."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch
from pipeline import plan_files as pf
from tests.support.orch_harness import OrchHarness

TASKS_TEXT = (
    "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n"
    "## Graph\nwave 1: task-1\nflags: no-merge no-test no-audit no-retro\n"
)


class OrchNameTest(OrchHarness):
    def setUp(self):
        super().setUp()
        self.feat = os.path.join(self.d, ".work", "F")
        for n in ("01-a", "02-b"):
            os.makedirs(os.path.join(self.feat, "phases", n))
            with open(os.path.join(self.feat, "phases", n, "TASKS.md"), "w") as f:
                f.write(TASKS_TEXT)
        with open(os.path.join(self.feat, "Plan.md"), "w") as f:
            f.write("## Phases\n" + pf.PHASE_HEAD + "| 01-a | g | true | | | done | 1 |\n| 02-b | g | true | 01-a | | todo | 1 |\n")

    def phase_log(self, n):
        return os.path.join(self.feat, "phases", n, "orch.jsonl")

    def events(self, n):
        path = self.phase_log(n)
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def run_cmd(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli_orch.main([*args, "--dir", self.d])
        return json.loads(out.getvalue())

    def test_skip_name_writes_to_open_phase(self):
        self.run_cmd("skip", "F", "--step", "task-1")
        self.assertEqual([e["ev"] for e in self.events("02-b")], ["skip"])
        self.assertEqual(self.events("01-a"), [])

    def test_retry_name_writes_to_open_phase(self):
        self.run_cmd("retry", "F", "--step", "task-1")
        self.assertEqual([e["ev"] for e in self.events("02-b")], ["retry"])
        self.assertEqual(self.events("01-a"), [])

    def test_old_stage_option_with_phase_path_still_works(self):
        self.run_cmd("skip", "--stage", "F/phases/02-b", "--step", "task-1")
        self.assertEqual([e["ev"] for e in self.events("02-b")], ["skip"])

    def test_phase_path_as_name_still_works(self):
        self.run_cmd("retry", "F/phases/02-b", "--step", "task-1")
        self.assertEqual([e["ev"] for e in self.events("02-b")], ["retry"])

    def test_all_phases_done_exits_with_message(self):
        with open(os.path.join(self.feat, "Plan.md"), "w") as f:
            f.write("## Phases\n" + pf.PHASE_HEAD + "| 01-a | g | true | | | done | 1 |\n| 02-b | g | true | 01-a | | done | 1 |\n")
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd("skip", "F", "--step", "task-1")
        self.assertIn("F", str(cm.exception))
        self.assertEqual(self.events("01-a"), [])
        self.assertEqual(self.events("02-b"), [])

    def test_no_name_and_no_stage_exits_with_message(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd("retry", "--step", "task-1")
        self.assertIn("NAME", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
