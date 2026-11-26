"""orch phases, group 2 of 4: see orch_harness.PhaseHarness (split from test_orch.py to keep each file fast)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orch_harness import PhaseHarness, start_index_patch, stop_index_patch
from pipeline import graph, tasks


def setUpModule():
    start_index_patch()


def tearDownModule():
    stop_index_patch()


class PhaseTest(PhaseHarness, unittest.TestCase):
    def test_reaudit_is_haiku_low_when_fixes_are_small(self):
        self.audited()
        p = self.batch()
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write("| task-2 | fix | dev | 1 | wave 2 |\nwave 2: task-2\n")
        self.answer(p[0], "VERDICT: DONE")
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "DONE")
        r = self.batch_agents()
        self.assertEqual((r[0]["agent"], r[0]["model"], r[0].get("effort")), ("sec-auditor", "haiku", "low"))
        self.assertIn("This is a re-audit: review only the findings in .work/s/audits/audit-sec.md", r[0]["prompt"])
        self.assertIn("Do not rerun the full tool scans", r[0]["prompt"])

    def test_again_fixes_after_reaudit_goes_to_main(self):
        self.audited()
        self.plan_fix_tasks()
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "DONE")
        self.run_batch(["sec-auditor"], "FIXES")
        self.plan_round2("audit-sec-2.md")  # second fix round is allowed
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")
        self.run_batch(["merger"], "DONE")
        d = self.batch_agents()
        self.assertEqual((d[0]["agent"], d[0]["model"]), ("sec-auditor", "haiku"))  # re-audit of small fixes: Haiku
        self.answer(d[0], "VERDICT: FIXES")
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("audit-sec-3", r["message"])

    def test_audit_effort_flags(self):
        t = f"{self.d}/.work/s/TASKS.md"
        with open(t, "a", encoding="utf-8") as f:
            f.write("flags: audit-low\n")
        self.assertEqual(tasks.parse_flags(t)["effort"], "low")
        with open(t, "a", encoding="utf-8") as f:
            f.write("flags: audit-high\n")
        self.assertEqual(tasks.parse_flags(t)["effort"], "low")  # high without a reason is ignored
        with open(t, "a", encoding="utf-8") as f:
            f.write("high-because: migration rewrites user data\n")
        flags = tasks.parse_flags(t)
        self.assertEqual((flags["effort"], flags["why"]), ("high", "migration rewrites user data"))
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        self.run_batch(["script"], "DONE")
        ds = self.batch()
        self.assertEqual({x["agent"]: x.get("effort") for x in ds}, {"sec-auditor": "high", "ux-reviewer": None, "arch-auditor": "high"})
        self.assertIn("migration rewrites user data", [x for x in ds if x["agent"] == "sec-auditor"][0]["prompt"])

    def test_tools_check_precedes_audits(self):
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        ds = self.run_batch(["script"], "DONE")
        self.assertIn("tools-check", ds[0]["prompt"])
        self.assertIn("xtools.py check --install", ds[0]["prompt"])
        self.run_batch(["arch-auditor", "sec-auditor", "ux-reviewer"], "CLEAN", "CLEAN", "CLEAN")  # one first pass

    def test_post_publish_steps_off_by_default(self):
        self.post("")
        self.tailpost()
        self.assertEqual(self.go("next")["action"], "done")

    def test_index_script_runs_once_after_wave_merge_and_failure_only_warns(self):
        calls = []
        with mock.patch.object(graph, "run_index", side_effect=lambda a: calls.append(1) or 1):
            self.base()
            self.run_batch(["merger"], "DONE")
            self.go("next")
            self.go("next")
        self.assertEqual(len(calls), 1)

    def test_master_merged_pause_comes_before_push(self):
        self.post("")
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("master-merged", r["message"])

    def test_no_index_flag_turns_index_off(self):
        self.post("flags: no-index\n")
        self.go("skip", "--step", "master-merged")
        self.run_batch(["ops"], "DONE")
        self.run_batch(["script"], "DONE")
        self.assertEqual(self.go("next")["action"], "done")


if __name__ == "__main__":
    unittest.main()
