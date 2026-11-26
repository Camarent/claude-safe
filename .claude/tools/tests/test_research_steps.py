"""`Type: research` tasks: the scout (Haiku, read-only) answers, research_apply.py commits the answer, the task's Test lines check it."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_files, steps
from tests.support.tmpdirs import tracked_mkdtemp

TASK = {"id": "task-1", "owner_sonnet": False, "review_sonnet": False}


def ev(kind, **kw):
    return {"ev": kind, "task": "task-1", **kw}


class ResearchTaskTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        Path(self.d, ".work", "s").mkdir(parents=True)
        self.a = SimpleNamespace(stage="s", dir=self.d)
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"worktrees": True, "gate": False, "testrun": True, "qualityrun": False, "prereview": False, "gates": []}), encoding="utf-8"
        )
        self.task_file("# task-1: where is X\nType: research\nOutputs: docs/research/x.md\nTest: test -s docs/research/x.md\n")

    def task_file(self, text):
        Path(self.d, ".work", "s", "task-1.md").write_text(text, encoding="utf-8")

    def report(self, name, text):
        f = Path(self.d, name)
        f.write_text(text, encoding="utf-8")
        return str(f)

    def nxt(self, mine):
        self.a.orch_events = mine
        return steps.next_step(self.a, TASK, mine, "a9")

    def scout_run(self, verdict, reason=""):
        return [
            ev("dispatch", id="a1", agent="scout", role="scout", model="haiku", why=None),
            ev("report", id="a1", role="scout", verdict=verdict, reason=reason, file=self.report("s.md", f"VERDICT: {verdict}\nbody\n")),
        ]

    def apply_run(self, verdict):
        return [
            *self.scout_run("DONE"),
            ev("dispatch", id="a2", agent="script", role="script", model=None, why="research apply (the scout's report committed)"),
            ev("report", id="a2", role="script", verdict=verdict, file="apply.md"),
        ]

    def test_type_is_known_to_the_plan_and_read_by_is_research(self):
        self.assertIn("research", plan_files.TASK_TYPES)
        self.assertTrue(steps.is_research(self.a, TASK))
        self.task_file("# task-1: code\nType: code\n")
        self.assertFalse(steps.is_research(self.a, TASK))

    def test_first_step_is_the_scout_on_haiku_not_a_dev(self):
        r = self.nxt([])
        self.assertEqual((r["action"], r["agent"], r["role"], r["model"]), ("dispatch", "scout", "scout", "haiku"))
        self.assertNotIn("E1", r.get("why", ""))
        self.assertIn(f".work/s/{TASK['id']}.md", r["prompt"])
        self.assertIn("VERDICT: DONE|BLOCKED", r["prompt"])
        self.assertIn("no Write tool", r["prompt"])

    def test_scout_done_commits_the_report_by_research_apply(self):
        r = self.nxt(self.scout_run("DONE"))
        self.assertEqual((r["action"], r["role"]), ("dispatch", "script"))
        self.assertIn("research apply", r["why"])
        self.assertIn(f"research_apply.py --stage s --task {TASK['id']}", r["prompt"])
        self.assertIn("--report", r["prompt"])
        self.assertIn("s.md", r["prompt"])

    def test_scout_other_verdict_goes_to_the_lead_with_the_report(self):
        for verdict in ("BLOCKED", "FAIL"):
            with self.subTest(verdict=verdict):
                r = self.nxt(self.scout_run(verdict, "no source"))
                self.assertEqual(r["action"], "ask_main")
                self.assertIn(f"scout verdict {verdict}", r["message"])
                self.assertIn("s.md", r["message"])

    def test_dispatched_scout_never_reported_goes_to_the_lead(self):
        r = self.nxt([ev("dispatch", id="a1", agent="scout", role="scout", model="haiku", why=None)])
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("never reported", r["message"])

    def test_apply_done_runs_the_test_lines_by_script(self):
        r = self.nxt(self.apply_run("DONE"))
        self.assertEqual((r["action"], r["role"]), ("dispatch", "script"))
        self.assertIn(f"testrun.py --stage s --task {TASK['id']}", r["prompt"])

    def test_apply_done_without_testrun_ends_the_task(self):
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"worktrees": True, "gate": False, "testrun": False, "qualityrun": False, "prereview": False, "gates": []}), encoding="utf-8"
        )
        self.assertIsNone(self.nxt(self.apply_run("DONE")))

    def test_apply_fail_goes_to_the_lead_with_the_report(self):
        r = self.nxt(self.apply_run("FAIL"))
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("research apply FAIL", r["message"])
        self.assertIn("apply.md", r["message"])

    def test_test_lines_done_ends_the_task_without_review_or_merge(self):
        mine = [
            *self.apply_run("DONE"),
            ev("dispatch", id="a3", agent="script", role="script", model=None, why="test run (the task's Test lines check the scout's report)"),
            ev("report", id="a3", role="script", verdict="DONE", file="t.md"),
        ]
        self.assertIsNone(self.nxt(mine))

    def test_test_lines_fail_goes_to_the_lead_once(self):
        mine = [
            *self.apply_run("DONE"),
            ev("dispatch", id="a3", agent="script", role="script", model=None, why="test run (the task's Test lines check the scout's report)"),
            ev("report", id="a3", role="script", verdict="FAIL", file="t.md"),
        ]
        r = self.nxt(mine)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("Test lines failed on the scout's report", r["message"])


if __name__ == "__main__":
    unittest.main()
