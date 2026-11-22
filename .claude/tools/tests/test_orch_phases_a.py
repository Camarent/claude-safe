"""orch phases, group 1 of 4: see orch_harness.PhaseHarness (split from test_orch.py to keep each file fast)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orch_harness import PhaseHarness, start_index_patch, stop_index_patch

# task ids the PhaseHarness writes into TASKS.md; asserts build from these names
TASK_ONE = "task-1"
TASK_TWO = "task-2"


def setUpModule():
    start_index_patch()


def tearDownModule():
    stop_index_patch()


class PhaseTest(PhaseHarness, unittest.TestCase):
    def test_tools_check_fail_asks_main_and_audits_wait(self):
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "FAIL")
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("tools-check.md", r["message"])

    def test_tools_check_done_dispatches_audits(self):
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        self.run_batch(["script"], "DONE")
        self.assertEqual(len(self.batch()), 3)  # tools-check, ux-shots; then sec + ux + arch in one pass

    def test_stage_without_audits_has_no_tools_check(self):
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write("flags: no-audit\n")
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.tailpost()
        self.assertEqual(self.go("next")["action"], "done")

    def test_full_clean_run(self):
        self.base()
        m = self.run_batch(["merger"], "DONE")
        self.assertIn("wave 1", m[0]["prompt"])
        self.assertIn(f"{TASK_ONE} (dev report", m[0]["prompt"])
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        ds = self.run_batch(["arch-auditor", "sec-auditor", "ux-reviewer"], "CLEAN", "CLEAN", "CLEAN")  # one first pass; arch is first in sorted order
        self.assertEqual(
            {x["agent"]: x["model"] for x in ds}, {"arch-auditor": "sonnet", "sec-auditor": "sonnet", "ux-reviewer": "sonnet"}
        )  # sec and arch: Sonnet, Opus only with audit-high or after ESCALATE
        self.tailpost()
        self.assertEqual(self.go("next")["action"], "done")

    def test_audit_fixes_are_planned_by_sub_architect_and_run_as_normal_tasks(self):
        self.audited()
        self.plan_fix_tasks()
        d = self.run_batch(["dev"], "DONE")
        self.assertEqual(d[0]["model"], "haiku")  # plan text never picks Sonnet
        self.assertIn(f"{TASK_TWO}.md", d[0]["prompt"])  # model from the planner's row
        self.run_batch(["reviewer"], "PASS")
        m = self.run_batch(["merger"], "DONE")
        self.assertIn("wave 2", m[0]["prompt"])
        self.assertIn("nothing to merge = DONE", m[0]["prompt"])
        c = self.run_batch(["sec-auditor"], "CLEAN")
        self.assertIn("audit-sec-2.md", c[0]["prompt"])  # only the audit that asked for fixes runs again
        self.tailpost()
        self.assertEqual(self.go("next")["action"], "done")  # the re-audit was CLEAN

    def test_no_ux_block_no_ux_review(self):
        os.remove(f"{self.d}/AUDIT.md")
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        self.assertEqual(sorted(x["agent"] for x in self.batch()), ["arch-auditor", "sec-auditor"])

    def test_arch_audit_runs_in_the_first_pass_with_sec_and_ux(self):
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        ds = self.run_batch(["arch-auditor", "sec-auditor", "ux-reviewer"], "CLEAN", "CLEAN", "CLEAN")
        self.assertEqual(sorted(x["agent"] for x in ds), ["arch-auditor", "sec-auditor", "ux-reviewer"])  # one first pass

    def test_audit_context_script_runs_before_the_audits_and_is_named_in_the_prompt(self):
        with open(f"{self.d}/AUDIT.md", "a", encoding="utf-8") as f:
            f.write('```audit-tools\nv | . | printf "" | vulture\n```\n')  # audit tools configured: audit-run and audit-context run as scripts
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        scripts, auditors = [], []
        for _ in range(8):
            b = self.batch()
            if b[0]["agent"] == "script":
                scripts.append(b[0]["prompt"])
                self.answer(b[0], "VERDICT: DONE")
            else:
                auditors = b
                break
        self.assertTrue(any("audit_context.py" in p and "--base master --head dev" in p and "--workspace" in p for p in scripts), scripts)
        self.assertTrue(
            any("plan_context.py stage" in p and "--base master --head dev" in p and f"--workspace {os.path.abspath(self.d)}" in p for p in scripts), scripts
        )
        for ag in ("sec-auditor", "arch-auditor"):
            pr = next((x["prompt"] for x in auditors if x["agent"] == ag), None)
            if pr is not None:
                self.assertIn("audit-context.md", pr)
                self.assertIn("plan-vs-changes.md", pr)
                self.assertIn("plan-context.md", pr)


if __name__ == "__main__":
    unittest.main()
