"""Dev `NO FIX:` marker (one owner decision, no ladder) and `Type: webtest` tasks (the web-tester runs them, the Test lines check its report)."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testrun
from pipeline import plan_check, plan_files, plan_rules, steps
from tests.support.tmpdirs import tracked_mkdtemp

TASK = {"id": "task-1", "owner_sonnet": False, "review_sonnet": False}
WT_HEAD = "abc1234def5678" + "0" * 26  # the stage branch HEAD the web-tester reports carry


def ev(kind, **kw):
    return {"ev": kind, "task": "task-1", **kw}


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        Path(self.d, ".work", "s").mkdir(parents=True)
        self.a = SimpleNamespace(stage="s", dir=self.d)
        self.cfg()

    def cfg(self, **kw):
        base = {"worktrees": True, "gate": False, "testrun": True, "qualityrun": False, "prereview": False, "gates": []}
        Path(self.d, "pipeline.json").write_text(json.dumps({**base, **kw}), encoding="utf-8")

    def task_file(self, text):
        Path(self.d, ".work", "s", "task-1.md").write_text(text, encoding="utf-8")

    def report(self, name, text):
        f = Path(self.d, name)
        f.write_text(text, encoding="utf-8")
        return str(f)

    def nxt(self, mine):
        self.a.orch_events = mine
        return steps.next_step(self.a, TASK, mine, "a9")


class NoFixTest(_Base):
    def dev_run(self, verdict, text):
        return [
            ev("dispatch", id="a1", agent="dev", role="dev", model="haiku", why=None),
            ev("report", id="a1", role="dev", verdict=verdict, file=self.report("r.md", text)),
        ]

    def test_blocked_no_fix_asks_the_owner_once_with_the_reason(self):
        r = self.nxt(self.dev_run("BLOCKED", "VERDICT: BLOCKED\nBLOCKED: NO FIX: upstream pkg 2.1 has no fixed version\n"))
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("NO FIX, owner decision: upstream pkg 2.1 has no fixed version", r["message"])
        self.assertIn("No further tries run", r["message"])

    def test_escalate_no_fix_does_not_start_the_ladder(self):
        r = self.nxt(self.dev_run("ESCALATE", "VERDICT: ESCALATE\nESCALATE: NO FIX - the advisory has no patch\n"))
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("the advisory has no patch", r["message"])

    def test_plain_escalate_still_goes_up_the_ladder(self):
        r = self.nxt(self.dev_run("ESCALATE", "VERDICT: ESCALATE\nESCALATE: tests stay red after two tries\n"))
        self.assertEqual((r["action"], r["role"]), ("dispatch", "dev"))

    def test_no_fix_marker_in_a_done_report_is_ignored(self):
        r = self.nxt(self.dev_run("DONE", "VERDICT: DONE\nBLOCKED: NO FIX: quoted from the brief\n"))
        self.assertNotEqual(r["action"], "ask_main")


class WebtestTaskTest(_Base):
    def setUp(self):
        super().setUp()
        self.task_file("# task-1: smoke\nType: webtest\nTest: node check.js out.md\n")
        head = mock.patch.object(steps, "_branch_head", return_value=WT_HEAD)  # the report HEAD line must match the branch HEAD
        head.start()
        self.addCleanup(head.stop)

    def wt_run(self, verdict, reason=""):
        return [
            ev("dispatch", id="a1", agent="web-tester", role="web-tester", model="haiku", why=None),
            ev("report", id="a1", role="web-tester", verdict=verdict, reason=reason, file=self.report("wt.md", f"VERDICT: {verdict}\nHEAD: {WT_HEAD}\n")),
        ]

    def test_type_is_known_to_the_plan(self):
        self.assertIn("webtest", plan_files.TASK_TYPES)
        self.assertTrue(steps.is_webtest(self.a, TASK))

    def test_first_step_is_the_web_tester_not_a_dev(self):
        r = self.nxt([])
        self.assertEqual((r["action"], r["agent"], r["role"], r["model"]), ("dispatch", "web-tester", "web-tester", "haiku"))
        self.assertIn("--state-dir", r["prompt"])
        self.assertIn(f".work/s/{TASK['id']}.md", r["prompt"])
        self.assertIn("VERDICT: PASS|FAIL|BLOCKED", r["prompt"])

    def test_web_tester_pass_runs_the_test_lines_by_script(self):
        r = self.nxt(self.wt_run("PASS"))
        self.assertEqual((r["action"], r["role"]), ("dispatch", "script"))
        self.assertIn(f"testrun.py --stage s --task {TASK['id']}", r["prompt"])

    def test_test_lines_done_ends_the_task_without_review_or_gates(self):
        mine = [
            *self.wt_run("PASS"),
            ev("dispatch", id="a2", agent="script", role="script", model=None, why="test run (the task's Test lines ...)"),
            ev("report", id="a2", role="script", verdict="DONE", file="t.md"),
        ]
        self.assertIsNone(self.nxt(mine))

    def test_failed_test_lines_go_to_the_lead_once(self):
        mine = [
            *self.wt_run("PASS"),
            ev("dispatch", id="a2", agent="script", role="script", model=None, why="test run (the task's Test lines ...)"),
            ev("report", id="a2", role="script", verdict="FAIL", file="t.md"),
        ]
        r = self.nxt(mine)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("Test lines failed on the web-tester's report", r["message"])

    def test_blocked_or_failed_web_tester_goes_to_the_lead_not_to_a_dev(self):
        for verdict in ("BLOCKED", "FAIL"):
            r = self.nxt(self.wt_run(verdict, "no browser"))
            self.assertEqual(r["action"], "ask_main", verdict)
            self.assertIn(f"web-tester verdict {verdict}", r["message"])

    def test_lead_retry_after_a_failed_web_tester_starts_the_web_tester_again(self):
        evs = [*self.wt_run("BLOCKED", "no browser"), ev("retry", reason="lead retry")]
        r = self.nxt(steps.history(evs, "task-1"))
        self.assertEqual((r["action"], r["agent"]), ("dispatch", "web-tester"))

    def test_skip_ends_the_task(self):
        self.assertIsNone(self.nxt([ev("skip")]))

    def test_testrun_runs_a_webtest_task_on_the_main_checkout_not_a_job_worktree(self):
        self.assertEqual(testrun.workdir(self.d, "s", TASK["id"]), self.d)
        self.task_file(f"# {TASK['id']}\nType: code\n")
        self.assertIsNone(testrun.workdir(self.d, "s", TASK["id"]))  # a code task never tests the main checkout

    def test_other_task_types_keep_the_dev_first(self):
        self.task_file("# task-1\nType: code\n")
        self.assertEqual(self.nxt([])["role"], "dev")


class WebtestPlanWarningTest(_Base):
    def warn(self, text):
        Path(self.d, "task-1.md").write_text(text, encoding="utf-8")
        return plan_rules.check_webtest_type(self.d, "task-1")

    def test_brief_run_by_the_web_tester_without_the_type_is_warned(self):
        out = self.warn("# t\nDone: Goal: run by the web-tester agent at 390x844\n")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith(f"webtest: {TASK['id']}"))
        self.assertIn("Type: webtest", out[0])

    def test_type_webtest_and_unrelated_mentions_are_quiet(self):
        self.assertEqual(self.warn("# t\nType: webtest\nDone: run by the web-tester\n"), [])
        self.assertEqual(self.warn("# t\nDone: the final web-tester pass checks the page later\n"), [])

    def test_the_warning_never_fails_a_check(self):
        self.assertEqual(plan_check.errors_of([f"webtest: {TASK['id']}: says 'x' but is not `Type: webtest`"]), [])


if __name__ == "__main__":
    unittest.main()
