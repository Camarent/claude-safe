"""orch phases, group 4 of 4: see orch_harness.PhaseHarness (split from test_orch.py to keep each file fast)."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orch_harness import PhaseHarness, argparse_ns, start_index_patch, stop_index_patch
from pipeline import cli_orch, tasks, triggers
from pipeline.util import work_path


def setUpModule():
    start_index_patch()


def tearDownModule():
    stop_index_patch()


class PhaseTest(PhaseHarness, unittest.TestCase):
    def test_trigger_log_records_why_and_outcome(self):
        self.audited()
        self.plan_fix_tasks()
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "DONE")
        self.run_batch(["sec-auditor"], "CLEAN")
        with open(f"{self.d}/.work/triggers.jsonl", encoding="utf-8") as f:
            recs = [json.loads(line) for line in f]
        trig = [r for r in recs if r["ev"] == "trigger"]
        self.assertEqual(
            [r["agent"] for r in trig], ["sec-auditor", "ux-reviewer", "arch-auditor", "sec-auditor"]
        )  # audits (sec runs again after its fixes) only (architect on Sonnet is no trigger); no dev/reviewer/merger
        out = {r["step"]: r["verdict"] for r in recs if r["ev"] == "outcome"}
        self.assertEqual(out["audit-sec"], "FIXES")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_orch.main(["triggers", "--stage", "s", "--dir", self.d])
        self.assertIn("audit-sec", buf.getvalue())
        self.assertIn("important=yes", buf.getvalue())
        self.assertIn("important=no", buf.getvalue())

    def test_retro_signals_and_done_message(self):
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write("high-because: formats change\nflags: audit-high\n")
        self.audited()
        self.plan_fix_tasks()
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "DONE")
        self.run_batch(["sec-auditor"], "CLEAN")
        self.tailpost()
        r = self.go("next")
        self.assertEqual(r["action"], "done")
        for name in ("high-effort", "audit-fixes", "fix-share"):  # 1 fix task vs 1 base task
            self.assertIn(name, r["message"])
        self.assertIn("retro needed", r["message"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_orch.main(["retro", "--stage", "s", "--dir", self.d])
        self.assertIn("RETRO audit-fixes: sec/arch audits asked for fixes: audit-sec", buf.getvalue())
        self.assertIn("retro.md", buf.getvalue())
        rp = work_path(os.path.join(self.d, ".work", "s"), "retro.md", create=True)
        with open(rp, "w") as f:
            f.write("done\n")
        self.assertNotIn("retro needed", self.go("next")["message"])

    def test_clean_stage_has_no_retro_and_stuck_counts(self):
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        self.run_batch(["sec-auditor", "ux-reviewer"], "CLEAN", "CLEAN")
        self.run_batch(["arch-auditor"], "CLEAN")
        self.tailpost()
        r = self.go("next")
        self.assertEqual(r["action"], "done")
        self.assertNotIn("retro", r["message"])
        self.go("skip", "--step", "final-test")  # any retry/skip is a signal
        self.assertIn("stuck", [n for n, _ in triggers.retro_signals(argparse_ns(self.d))])

    def test_retro_numbers_step_runs_last_and_feeds_signals(self):
        t = f"{self.d}/.work/s/TASKS.md"
        txt = Path(t).read_text(encoding="utf-8").replace("flags: ui docs no-retro", "flags: no-merge no-test no-audit")  # retro stays on
        Path(t).write_text(txt, encoding="utf-8")
        self.base()
        self.go("skip", "--step", "master-merged")
        self.run_batch(["script"], "DONE")
        self.run_batch(["script"], "DONE")
        d = self.batch()
        self.assertEqual((d[0]["agent"], d[0]["model"]), ("script", None))
        self.assertIn("retro_numbers.py", d[0]["prompt"])
        os.makedirs(f"{self.d}/.work/s/retro", exist_ok=True)
        Path(f"{self.d}/.work/s/retro/retro-numbers.md").write_text("RETRO context: lead peak context 180k tokens\n-- table\n", encoding="utf-8")
        self.answer(d[0], "VERDICT: DONE")
        d = self.batch()
        self.assertEqual((d[0]["agent"], d[0]["model"]), ("ops", "haiku"))
        self.assertIn("retro-digest.md", d[0]["prompt"])
        self.answer(d[0], "VERDICT: DONE")
        r = self.go("next")
        self.assertEqual(r["action"], "done")
        self.assertIn("context", r["message"])  # transcript signal reaches the finished line

    def test_second_ux_fixes_goes_to_main(self):
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        self.run_batch(["sec-auditor", "ux-reviewer"], "CLEAN", "FIXES")
        self.run_batch(["arch-auditor"], "CLEAN")
        self.plan_fix_tasks_for("audit-ux.md")
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "DONE")
        self.run_batch(["ux-reviewer"], "FIXES")
        self.plan_round2("audit-ux-2.md")
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "DONE")
        self.run_batch(["ux-reviewer"], "FIXES")
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("audit-ux-3", r["message"])

    def test_ux_recheck_runs_on_haiku(self):
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        self.run_batch(["sec-auditor", "ux-reviewer"], "CLEAN", "FIXES")
        self.run_batch(["arch-auditor"], "CLEAN")
        self.plan_fix_tasks_for("audit-ux.md")
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "DONE")
        self.run_batch(["script"], "DONE")  # ux-shots-2
        d = self.batch_agents()
        self.assertEqual((d[0]["agent"], d[0]["model"], d[0]["effort"]), ("ux-reviewer", "haiku", "low"))

    def test_blocked_and_failed_steps_go_to_main_and_retry_skip_resume(self):
        self.run_batch(["dev"], "BLOCKED")
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("retry --stage s --step task-1", r["message"])
        self.go("retry", "--step", "task-1", "--note", "I fixed the env var")
        d = self.batch()
        self.assertTrue(d[0]["prompt"].startswith("Lead note (read first): I fixed the env var"))
        self.answer(d[0], "VERDICT: DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "FAIL")  # conflict or red tests: one Sonnet fix and one more merge before the lead
        fix = self.batch()
        self.assertEqual((fix[0]["agent"], fix[0]["model"]), ("merger", "sonnet"))
        self.answer(fix[0], "VERDICT: DONE")
        self.run_batch(["merger"], "FAIL")
        self.assertEqual(self.go("next")["action"], "ask_main")
        self.go("skip", "--step", "merge-w1-2")
        self.assertIn("tester", [x["agent"] for x in self.batch()])

    def test_flags_turn_phases_off(self):
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write("flags: no-merge no-test no-audit no-retro\n")
        self.base()
        self.assertEqual(self.go("next")["action"], "dispatch")  # docs only: docs flag still on
        self.assertEqual(tasks.parse_flags(f"{self.d}/.work/s/TASKS.md")["audit"], False)


if __name__ == "__main__":
    unittest.main()
