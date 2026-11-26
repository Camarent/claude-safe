"""orch skip of a step that timed out (its report says REASON): refused without --note, accepted with a note the event and the PR body keep."""

import argparse
import json
import os
import sys
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import cli_orch, pr_ops  # noqa: E402 -- sys.path is set above
from pipeline.slow_suite import REASON  # noqa: E402 -- sys.path is set above
from pipeline.util import append_event, read_jsonl  # noqa: E402 -- sys.path is set above
from tests.support.orch_harness import OrchHarness  # noqa: E402 -- sys.path is set above
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402 -- sys.path is set above

SLOW = "slow-step"
QUICK = "quick-step"


class SkipTimedOutTest(unittest.TestCase):
    def setUp(self):
        self.t = tracked_mkdtemp()
        os.makedirs(f"{self.t}/.work/s")
        with open(f"{self.t}/.work/s/tasks.md", "w", encoding="utf-8") as fh:
            fh.write("")
        self.report_of(SLOW, f"VERDICT: BLOCKED\n{REASON}: acceptance cmd hit 900 s\n")
        self.report_of(QUICK, "VERDICT: DONE\nall green\n")

    def report_of(self, task, text):
        f = f"{self.t}/.work/s/report-{task}.md"
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(text)
        append_event(f"{self.t}/.work/s/orch.jsonl", {"ev": "report", "id": f"{task}-r", "task": task, "role": "dev", "verdict": "BLOCKED", "file": f})

    def skip(self, task, note=""):
        a = argparse.Namespace(cmd="skip", stage="s", dir=self.t, step=task, note=note, from_step="")
        cli_orch.cmd_reset(a)

    def log(self):
        with open(f"{self.t}/.work/s/orch.jsonl", encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def skips(self):
        return [e for e in self.log() if e["ev"] == "skip"]

    def test_timed_out_skip_without_note_is_refused_and_writes_nothing(self):
        before = len(self.log())
        with self.assertRaises(SystemExit) as cm:
            self.skip(SLOW)
        self.assertIn('refused: it timed out, not verified; pass --note "who verified it, how"', str(cm.exception))
        self.assertEqual(len(self.log()), before)
        self.assertEqual(self.skips(), [])

    def test_timed_out_skip_with_note_is_recorded_with_the_note(self):
        self.skip(SLOW, note="alice ran the suite by hand, green")
        [ev] = self.skips()
        self.assertEqual(ev["task"], SLOW)
        self.assertEqual(ev["note"], "alice ran the suite by hand, green")
        self.assertIs(ev.get("timed_out"), True)

    def test_other_steps_skip_without_a_note_as_before(self):
        self.skip(QUICK)
        [ev] = self.skips()
        self.assertEqual(ev["task"], QUICK)
        self.assertNotIn("timed_out", ev)

    def test_skipped_notes_lists_one_line_per_timed_out_skip_with_its_note(self):
        self.assertEqual(pr_ops.skipped_notes(self.t, "s"), "")
        self.skip(SLOW, note="alice ran it, green")
        self.skip(QUICK)
        self.assertEqual(pr_ops.skipped_notes(self.t, "s"), f"SKIPPED {SLOW} ({REASON}): alice ran it, green")

    def test_skipped_notes_empty_for_missing_stage(self):
        self.assertEqual(pr_ops.skipped_notes(self.t, "nope"), "")


class SkipViaMainTest(OrchHarness):
    """Drive `orch skip` through main() and `orch next` on a one-task stage."""

    def timed_out_task(self):
        d = self.next()
        self.answer(d, f"VERDICT: BLOCKED\n{REASON}: acceptance cmd hit 900 s\n")

    def test_main_skip_without_note_is_refused(self):
        self.timed_out_task()
        with self.assertRaises(SystemExit) as cm:
            self.go("skip", "--step", "task-1")
        self.assertIn("refused: it timed out", str(cm.exception))

    def test_next_does_not_redispatch_a_noted_timed_out_skip(self):
        self.timed_out_task()
        self.go("skip", "--step", "task-1", "--note", "alice ran it by hand, green")
        evs = read_jsonl(f"{self.d}/.work/s/orch.jsonl")
        [ev] = [e for e in evs if e["ev"] == "skip"]
        self.assertEqual(ev["note"], "alice ran it by hand, green")
        before = [e for e in evs if e["ev"] == "dispatch" and e["task"] == "task-1"]
        self.go("next")
        after = [e for e in read_jsonl(f"{self.d}/.work/s/orch.jsonl") if e["ev"] == "dispatch" and e["task"] == "task-1"]
        self.assertEqual(len(after), len(before))  # no new dispatch of the skipped step


if __name__ == "__main__":
    unittest.main()
