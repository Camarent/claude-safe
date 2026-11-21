"""Characterization tests for pipeline.steps.next_step / run_phase and pipeline.graph.decide (branch by branch, exact outputs)."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import graph, steps, worktree
from pipeline.configuration import EXTRA_ROUND_WHY

TASK = {"id": "task-1", "owner_sonnet": False, "review_sonnet": False}


def ev(kind, **kw):
    return {"ev": kind, "task": "task-1", **kw}


def dev_rep(i, verdict, file="d.md"):
    return ev("report", id=i, role="dev", verdict=verdict, file=file)


def rev_rep(i, verdict, file="r.md"):
    return ev("report", id=i, role="reviewer", verdict=verdict, file=file)


def dispatch(i, role, model="haiku", why=None):
    return ev("dispatch", id=i, agent=role, role=role, model=model, why=why)


class NextStepTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.a = SimpleNamespace(stage="s", dir=self.d)
        self.cfg()

    def cfg(self, **kw):
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"gate": False, "testrun": False, "qualityrun": False, **kw}), encoding="utf-8"
        )  # the test run is off unless a test turns it on

    def nxt(self, mine, task=TASK, **kw):
        return steps.next_step(self.a, task, mine, "a9", **kw)

    def who(self, r):
        return (r["action"], r.get("role"), r.get("model"))

    def test_missing_gates_line_is_rework_not_pass(self):
        self.cfg(gates=[{"name": "lint", "cmd": "true"}])
        rep = Path(self.d, "d.md")
        rep.write_text("VERDICT: DONE\nfiles\n", encoding="utf-8")
        r = self.nxt([dispatch("a1", "dev"), dev_rep("a1", "DONE", str(rep))])
        self.assertEqual(self.who(r), ("dispatch", "dev", "haiku"))
        self.assertIn("Gates:", Path(r["prompt"].split("fix the findings in ")[1].split(" first")[0]).read_text(encoding="utf-8"))
        rep.write_text("VERDICT: DONE\nGates: lint RAN ok\n", encoding="utf-8")
        r = self.nxt([dispatch("a1", "dev"), dev_rep("a1", "DONE", str(rep))])
        self.assertEqual(r["agent"], "reviewer")

    def test_dev_brief_names_gates(self):
        self.cfg(gates=[{"name": "lint", "cmd": "mylint --strict"}])
        self.assertIn("mylint --strict", self.nxt([])["prompt"])

    def test_passed_or_skipped_is_none(self):
        self.assertIsNone(self.nxt([rev_rep("a1", "PASS")]))
        self.assertIsNone(self.nxt([dev_rep("a1", "FAIL"), ev("skip")]))

    def test_no_events_starts_the_dev(self):
        self.assertEqual(self.who(self.nxt([])), ("dispatch", "dev", "haiku"))
        self.assertEqual(self.who(self.nxt([], {**TASK, "owner_sonnet": True})), ("dispatch", "dev", "sonnet"))

    def test_unreported_dispatch_asks_the_lead(self):
        r = self.nxt([dispatch("a1", "dev")])
        self.assertEqual(r["action"], "ask_main")
        self.assertEqual(
            r["message"],
            "a1 (dev on task-1) was dispatched but never reported; fix the cause, then `orch.py retry --stage s --step task-1` (or `skip`)",
        )

    def test_dev_done_goes_to_reviewer_or_prereview(self):
        mine = [dispatch("a1", "dev"), dev_rep("a1", "DONE")]
        self.assertEqual(self.who(self.nxt(mine)), ("dispatch", "reviewer", "haiku"))
        self.assertEqual(self.who(self.nxt(mine, {**TASK, "review_sonnet": True})), ("dispatch", "reviewer", "sonnet"))
        self.cfg(prereview=True)
        r = self.nxt(mine)
        self.assertEqual((r["agent"], r["model"], r["why"]), ("script", None, "local pre-review (lp review); FAIL goes back to the dev"))
        self.assertIn("localmodel/prereview.py --stage s --task task-1 --dir " + self.d, r["prompt"])

    def test_test_run_after_every_dev_done(self):
        self.cfg(testrun=True)
        r = self.nxt([dispatch("a1", "dev"), dev_rep("a1", "DONE")])
        self.assertEqual((r["action"], r["role"]), ("dispatch", "script"))
        self.assertIn("testrun.py", r["prompt"])
        green = [
            dispatch("a1", "dev"),
            dev_rep("a1", "DONE"),
            dispatch("a2", "script", model=None, why="test run (x)"),
            ev("report", id="a2", role="script", verdict="DONE", file="t.md"),
        ]
        self.assertEqual(self.who(self.nxt(green)), ("dispatch", "reviewer", "haiku"))
        t1, t2 = os.path.join(self.d, "t.md"), os.path.join(self.d, "t2.md")
        for f in (t1, t2):
            Path(f).write_text("VERDICT: FAIL\n`node --test x` exited 1:\nnot ok 1 - x\n", encoding="utf-8")  # a real red run: class code
        red = [*green[:3], ev("report", id="a2", role="script", verdict="FAIL", file=t1)]
        self.assertEqual(self.who(self.nxt(red)), ("dispatch", "dev", "haiku"))
        again = [*red, dispatch("a3", "dev"), dev_rep("a3", "DONE")]
        self.assertEqual(self.nxt(again)["role"], "script")  # the rework is tested again
        twice = [*again, dispatch("a4", "script", model=None, why="test run (x)"), ev("report", id="a4", role="script", verdict="FAIL", file=t2)]
        self.assertEqual(self.who(self.nxt(twice)), ("dispatch", "dev", "sonnet"))  # a second red test run escalates like a dev ESCALATE

    def test_test_run_plan_defect_goes_to_lead_not_dev(self):
        self.cfg(testrun=True)
        os.makedirs(os.path.join(self.d, ".work", "s"), exist_ok=True)
        Path(self.d, ".work", "s", "task-1.md").write_text("Predicted paths: README.md\nTest: node --test tests/*.test.mjs\n", encoding="utf-8")
        out = os.path.join(self.d, "t.md")
        Path(out).write_text(
            "VERDICT: FAIL\n`qrun -- node --test tests/*.test.mjs` exited 3:\nSKIPPED/EMPTY: zero tests detected (0 tests)\n", encoding="utf-8"
        )
        red = [
            dispatch("a1", "dev"),
            dev_rep("a1", "DONE"),
            dispatch("a2", "script", model=None, why="test run (x)"),
            ev("report", id="a2", role="script", verdict="FAIL", file=out),
        ]
        r = self.nxt(red)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("class plan", r["message"])

    def test_escalation_gate_is_a_one_way_veto_before_sonnet(self):
        self.cfg(testrun=True, gate=True)
        t1, t2, g = (os.path.join(self.d, n) for n in ("t.md", "t2.md", "g.md"))
        for f in (t1, t2):
            Path(f).write_text("VERDICT: FAIL\n`node --test x` exited 1:\nnot ok 1 - x\n", encoding="utf-8")  # class code: the ladder is allowed
        base = [
            dispatch("a1", "dev"),
            dev_rep("a1", "DONE"),
            dispatch("a2", "script", model=None, why="test run (x)"),
            ev("report", id="a2", role="script", verdict="FAIL", file=t1),
            dispatch("a3", "dev"),
            dev_rep("a3", "DONE"),
            dispatch("a4", "script", model=None, why="test run (x)"),
            ev("report", id="a4", role="script", verdict="FAIL", file=t2),
        ]
        r = self.nxt(base)  # the second red run would start Sonnet: the gate asks first
        self.assertEqual((r["action"], r["role"]), ("dispatch", "script"))
        self.assertIn("escalation_gate.py", r["prompt"])
        self.assertIn(f"--failure {t1} --failure {t2}", r["prompt"])  # every red run so far, oldest first
        asked = [*base, dispatch("a5", "script", model=None, why="escalation gate (lp judge)")]
        Path(g).write_text("VERDICT: FAIL\njudge FAIL: stale file | BLOCKED.md\n", encoding="utf-8")
        veto = self.nxt([*asked, ev("report", id="a5", role="script", verdict="FAIL", file=g)])
        self.assertEqual(veto["action"], "ask_main")
        self.assertIn("vetoed by the local judge", veto["message"])
        go = self.nxt([*asked, ev("report", id="a5", role="script", verdict="DONE", file=g)])
        self.assertEqual(self.who(go), ("dispatch", "dev", "sonnet"))  # PASS or no answer: the ladder goes on as before

    def test_quality_run_is_advisory_and_once(self):
        self.cfg(qualityrun=True)
        r = self.nxt([dispatch("a1", "dev"), dev_rep("a1", "DONE")])
        self.assertEqual((r["action"], r["role"]), ("dispatch", "script"))
        self.assertIn("qualityrun.py --stage s --task task-1", r["prompt"])
        q = dispatch("a2", "script", model=None, why="quality run (x)")
        base = [dispatch("a1", "dev"), dev_rep("a1", "DONE"), q]
        clean = ev("report", id="a2", role="script", verdict="DONE", file="q.md")
        self.assertEqual(self.who(self.nxt([*base, clean])), ("dispatch", "reviewer", "haiku"))
        fail = ev("report", id="a2", role="script", verdict="FAIL", file="q.md")
        r = self.nxt([*base, fail])
        self.assertEqual(self.who(r), ("dispatch", "dev", "haiku"))
        self.assertIn("Rework: fix the findings in q.md first.", r["prompt"])
        after = [*base, fail, dispatch("a3", "dev"), dev_rep("a3", "DONE")]
        self.assertEqual(self.who(self.nxt(after)), ("dispatch", "reviewer", "haiku"))  # one extra round, no second quality run
        self.cfg(qualityrun=False)
        self.assertEqual(self.who(self.nxt([dispatch("a1", "dev"), dev_rep("a1", "DONE")])), ("dispatch", "reviewer", "haiku"))

    def test_prereview_result_decides(self):
        self.cfg(prereview=True)
        pre = dispatch("a2", "script", why="local pre-review (lp review)")
        base = [dispatch("a1", "dev"), dev_rep("a1", "DONE"), pre]
        fail = ev("report", id="a2", role="script", verdict="FAIL", file="pre.md")
        r = self.nxt([*base, fail])
        self.assertEqual(self.who(r), ("dispatch", "dev", "haiku"))
        self.assertIn("Rework: fix the findings in pre.md first.", r["prompt"])
        r = self.nxt([*base, fail], {**TASK, "owner_sonnet": True})
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))
        ok = ev("report", id="a2", role="script", verdict="DONE", file="pre.md")
        self.assertEqual(self.who(self.nxt([*base, ok])), ("dispatch", "reviewer", "haiku"))

    def test_skipped_prereview_is_no_pass(self):
        self.cfg(prereview=True)
        pre = dispatch("a2", "script", why="local pre-review (lp review)")
        skip = ev("report", id="a2", role="script", verdict="SKIPPED", file="pre.md", reason="diff too big")
        mine = [dispatch("a1", "dev"), dev_rep("a1", "DONE"), pre, skip]
        self.assertEqual(self.who(self.nxt(mine)), ("dispatch", "reviewer", "haiku"))  # default: the paid reviewer reviews
        self.cfg(prereview=True, prereview_skip="ask_main")
        r = self.nxt(mine)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("no review happened", r["message"])
        self.cfg(prereview=True)
        with mock.patch.dict(os.environ, {"PIPELINE_PREREVIEW_SKIP": "ask_main"}):
            self.assertEqual(self.nxt(mine)["action"], "ask_main")

    def test_dirty_rework_forbids_checkout(self):
        with mock.patch.object(steps, "job_dirty", return_value=["a.py"]):
            r = self.nxt([dispatch("a1", "dev"), dev_rep("a1", "DONE")])
        text = Path(r["prompt"].split("Rework: fix the findings in ")[1].split(" first")[0]).read_text(encoding="utf-8")
        self.assertIn("NEVER run `git checkout --`", text)
        self.assertNotIn("discard them", text)

    def test_dev_prompt_says_clean_worktree_and_no_checkout(self):
        r = self.nxt([])
        self.assertIn("clean worktree", r["prompt"])
        self.assertIn("Never run `git checkout --`", r["prompt"])

    def test_dev_escalate_paths(self):
        mine = [dispatch("a1", "dev"), dev_rep("a1", "ESCALATE", "e.md")]
        r = self.nxt(mine)
        self.assertEqual(self.who(r), ("dispatch", "dev", "haiku"))
        self.assertIn("Rework: fix the findings in e.md first.", r["prompt"])
        two = [*mine, dispatch("a2", "dev"), dev_rep("a2", "ESCALATE", "e2.md")]
        self.assertEqual(self.who(self.nxt(two)), ("dispatch", "dev", "sonnet"))
        # a Sonnet dev failing goes to the lead
        three = [*two, dispatch("a3", "dev", "sonnet"), dev_rep("a3", "ESCALATE", "e3.md")]
        self.assertEqual(self.nxt(three)["action"], "ask_main")

    def test_dev_blocked_on_a_reviewer_finding_without_change_stops_the_review_loop(self):
        mine = [
            dispatch("a1", "dev"),
            dev_rep("a1", "DONE"),
            dispatch("a2", "reviewer"),
            rev_rep("a2", "FAIL", "r2.md"),
            dispatch("a3", "dev", "sonnet"),
            dev_rep("a3", "BLOCKED", "b3.md"),
        ]
        with mock.patch.object(steps, "job_diff", return_value=""):
            r = self.nxt(mine)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("no further review round", r["message"])
        self.assertIn("r2.md", r["message"])
        with mock.patch.object(steps, "job_diff", return_value="diff"):  # the dev changed something: the ordinary message
            self.assertIn("dev verdict BLOCKED", self.nxt(mine)["message"])

    def test_dev_other_verdict_asks_the_lead(self):
        r = self.nxt([dispatch("a1", "dev"), dev_rep("a1", "BLOCKED", "b.md")])
        self.assertEqual(r["action"], "ask_main")
        self.assertTrue(r["message"].startswith("task-1: dev verdict BLOCKED ("))
        self.assertIn("report b.md; fix the cause, then `orch.py retry --stage s --step task-1`", r["message"])
        # owner_sonnet dev escalating also asks the lead
        r = self.nxt([dispatch("a1", "dev", "sonnet"), dev_rep("a1", "ESCALATE")], {**TASK, "owner_sonnet": True})
        self.assertEqual(r["action"], "ask_main")

    def test_reviewer_fail_rounds(self):
        d1 = [dispatch("a1", "dev"), dev_rep("a1", "DONE"), rev_rep("a2", "FAIL", "r1.md")]
        r = self.nxt(d1)
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))
        self.assertIn("Rework: fix the findings in r1.md first.", r["prompt"])
        d2 = [*d1, dispatch("a3", "dev", "sonnet"), dev_rep("a3", "DONE"), rev_rep("a4", "FAIL", "r2.md")]
        r = self.nxt(d2)  # second FAIL: one extra dev round with the verbatim findings
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))
        self.assertIn("reviews-all-task-1", r["prompt"])
        d3 = [*d2, dispatch("a5", "dev", "sonnet", why=EXTRA_ROUND_WHY), dev_rep("a5", "DONE"), rev_rep("a6", "FAIL", "r3.md")]
        r = self.nxt(d3)  # the extra round failed too: back to the lead
        self.assertEqual(r["action"], "ask_main")
        self.assertTrue(r["message"].startswith("task-1: review failed 3 rounds; findings r3.md; fix the cause"))

    def test_reviewer_escalate_then_sonnet_reviewer(self):
        mine = [dispatch("a1", "dev"), dev_rep("a1", "DONE"), dispatch("a2", "reviewer"), rev_rep("a2", "ESCALATE", "r1.md")]
        r = self.nxt(mine)
        self.assertEqual(self.who(r), ("dispatch", "reviewer", "sonnet"))  # a Haiku ESCALATE goes straight to Sonnet
        # a Sonnet reviewer already ran: an ESCALATE now counts as a failed round
        mine += [dispatch("a3", "reviewer", "sonnet"), rev_rep("a3", "ESCALATE", "r2.md")]
        r = self.nxt(mine)
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))
        self.assertIn("Rework: fix the findings in r2.md first.", r["prompt"])

    def test_sonnet_reviewer_escalate_loop_ends_at_the_lead(self):
        mine = [dispatch("a1", "dev"), dev_rep("a1", "DONE"), dispatch("a2", "reviewer", "sonnet"), rev_rep("a2", "ESCALATE", "r1.md")]
        for i in range(10):  # every Sonnet ESCALATE counts as a failed round, so the loop must end
            r = self.nxt(mine)
            if r["action"] == "ask_main":
                return
            mine += [
                dispatch(f"d{i}", "dev", r["model"]),
                dev_rep(f"d{i}", "DONE"),
                dispatch(f"r{i}", "reviewer", "sonnet"),
                rev_rep(f"r{i}", "ESCALATE", f"f{i}.md"),
            ]
        self.fail("Sonnet reviewer ESCALATE never reached the lead")

    def test_reviewer_other_verdict_ends_the_task_quietly(self):
        self.assertIsNone(self.nxt([dispatch("a1", "dev"), dev_rep("a1", "DONE"), rev_rep("a2", "BLOCKED")]))


class RunPhaseTest(unittest.TestCase):
    def setUp(self):
        self.a = SimpleNamespace(stage="s", dir=".")

    @staticmethod
    def st(sid, **kw):
        return {"id": sid, "agent": "ops", "model": "haiku", "prompt": "do {rid}", "ok": {"DONE"}, "effort": "low", "why": "w", **kw}

    def test_dispatch_numbers_follow_n(self):
        r = steps.run_phase(self.a, [], [self.st("x"), self.st("y")], 4)
        self.assertEqual(r["action"], "dispatch")
        self.assertEqual([d["id"] for d in r["dispatches"]], ["a5", "a6"])
        self.assertEqual(
            r["dispatches"][1], {"id": "a6", "agent": "ops", "model": "haiku", "task": "y", "role": "ops", "effort": "low", "why": "w", "prompt": "do a6"}
        )

    def test_satisfied_steps_give_none(self):
        evs = [{"ev": "report", "task": "x", "verdict": "DONE", "file": "f"}, {"ev": "skip", "task": "y"}, {"ev": "skip", "task": "z"}]
        self.assertIsNone(steps.run_phase(self.a, evs, [self.st("x"), self.st("y"), self.st("z", pause=True)], 0))

    def test_pause_asks_until_skipped(self):
        r = steps.run_phase(self.a, [], [self.st("p", pause=True, prompt="merge to master?")], 0)
        self.assertEqual(r, {"action": "ask_main", "message": "merge to master?"})

    def test_pending_and_failed_asks_are_joined(self):
        evs = [
            {"ev": "dispatch", "task": "x", "id": "a1"},
            {"ev": "report", "task": "y", "verdict": "FAIL", "file": "y.md", "reason": "boom"},
            {"ev": "report", "task": "tools-check", "verdict": "FAIL", "file": "t.md"},
        ]
        r = steps.run_phase(self.a, evs, [self.st("x"), self.st("y"), self.st("tools-check")], 1)
        lines = r["message"].split("\n")
        self.assertEqual(r["action"], "ask_main")
        self.assertEqual(lines[0], "x (ops) was dispatched but never reported; fix the cause, then `orch.py retry --stage s --step x` (or `skip`)")
        self.assertEqual(lines[1], "y: ops verdict FAIL (boom); report y.md; fix the cause, then `orch.py retry --stage s --step y` (or `skip`)")
        self.assertEqual(
            lines[2],
            "tools-check: ops verdict FAIL (); report t.md; tools missing, see .work/s/audits/tools-check.md; lead: retry or skip --step tools-check"
            "; fix the cause, then `orch.py retry --stage s --step tools-check` (or `skip`)",
        )

    def test_dispatch_wins_over_asks(self):
        evs = [{"ev": "dispatch", "task": "x", "id": "a1"}]
        r = steps.run_phase(self.a, evs, [self.st("x"), self.st("y")], 1)
        self.assertEqual(r["action"], "dispatch")
        self.assertEqual([d["task"] for d in r["dispatches"]], ["y"])


TASKS_MD = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |
{extra}
## Graph
wave 1: task-1{more}
flags: {flags}
PRE-APPROVED: master merge
"""


class DecideTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.w = Path(self.d, ".work", "s")
        self.w.mkdir(parents=True)
        self.a = SimpleNamespace(stage="s", dir=self.d)
        self.evs = []
        p = mock.patch.object(graph, "run_index", return_value=0)
        self.run_index = p.start()
        self.addCleanup(p.stop)

    def tasks(self, flags="no-merge no-test no-audit no-retro no-index no-push no-clean", extra="", more=""):
        (self.w / "TASKS.md").write_text(TASKS_MD.format(flags=flags, extra=extra, more=more), encoding="utf-8")

    def log(self, *events):
        self.evs.extend(events)
        (self.w / "orch.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.evs), encoding="utf-8")

    def passed(self):
        self.log(dispatch("a1", "dev"), dev_rep("a1", "DONE", "d1.md"), dispatch("a2", "reviewer"), rev_rep("a2", "PASS"))

    def test_webtest_task_runs_through_a_wave_to_its_merge_without_a_dev(self):
        (self.w / "task-1.md").write_text("# task-1\nType: webtest\nTest: node check.js\n", encoding="utf-8")
        self.tasks(flags="no-test no-audit no-retro no-index no-push no-clean")
        first = graph.decide(self.a)
        self.assertEqual((first["action"], first["dispatches"][0]["agent"]), ("dispatch", "web-tester"))
        self.log(
            dispatch("a1", "web-tester"),
            ev("report", id="a1", role="web-tester", verdict="PASS", file="w.md"),
            dispatch("a2", "script", model=None, why="test run (the task's Test lines check the web-tester's report on the stage branch checkout)"),
            ev("report", id="a2", role="script", verdict="DONE", file="t.md"),
        )
        r = graph.decide(self.a)
        self.assertEqual((r["action"], r["dispatches"][0]["task"]), ("dispatch", "merge-w1"))  # the task passed: only the wave merge is left

    def test_unparsed_tasks_ask(self):
        (self.w / "TASKS.md").write_text("- odd task-1 something\n", encoding="utf-8")  # not a task line: the id must follow the bullet
        self.assertEqual(graph.decide(self.a), {"action": "ask_main", "message": "TASKS.md names tasks but none parsed (bad format?)"})

    def test_empty_plan_is_done(self):
        (self.w / "TASKS.md").write_text("nothing here\n", encoding="utf-8")
        self.assertEqual(graph.decide(self.a), {"action": "done", "message": "no tasks"})

    def test_max_tasks_limit(self):
        self.tasks(extra="| task-2 | b | dev | 1 | wave W1, haiku |", more=", task-2")
        Path(self.d, "pipeline.json").write_text('{"max_tasks": 1}', encoding="utf-8")
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertTrue(r["message"].startswith("TASKS.md has 2 tasks not yet started, the limit is 1 (max_tasks in pipeline.json): split the stage"))
        # fix tasks of a fix round come on top: a fix-plan report lifts the limit
        self.log({"ev": "report", "task": "fix-plan-1", "role": "architect", "verdict": "DONE", "file": "f"})
        self.assertEqual(graph.decide(self.a)["action"], "dispatch")

    def test_max_tasks_counts_only_tasks_not_yet_started(self):
        self.tasks(extra="| task-2 | b | dev | 1 | wave W1, haiku |", more=", task-2")
        Path(self.d, "pipeline.json").write_text('{"max_tasks": 1}', encoding="utf-8")
        self.log({"ev": "skip", "task": "task-1"})
        self.assertNotIn("not yet started", graph.decide(self.a).get("message", ""))

    def test_wave_dispatches_in_parallel_and_asks_otherwise(self):
        self.tasks(extra="| task-2 | b | dev | 1 | wave W1, haiku |", more=", task-2")
        r = graph.decide(self.a)
        self.assertEqual([(d["id"], d["task"]) for d in r["dispatches"]], [("a1", "task-1"), ("a2", "task-2")])
        self.log(dispatch("a1", "dev"), ev("dispatch", id="a2", agent="dev", role="dev", model="haiku", why=None) | {"task": "task-2"})
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertEqual(r["message"].count("was dispatched but never reported"), 2)
        self.assertEqual(len(r["message"].split("\n")), 2)

    def test_all_passed_no_flags_is_done(self):
        self.tasks()
        self.passed()
        self.assertEqual(graph.decide(self.a), {"action": "done", "message": "all 1 task(s) reviewed PASS"})

    def test_merge_ladder_and_index(self):
        self.tasks(flags="no-test no-audit no-retro no-push no-clean")
        self.passed()
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "dispatch")
        (m,) = r["dispatches"]
        self.assertEqual((m["task"], m["agent"], m["model"], m["id"]), ("merge-w1", "merger", "haiku", "a3"))
        self.assertIn("task-1 (dev report d1.md)", m["prompt"])
        # merger FAIL -> one Sonnet fix, then a second merge
        self.log(
            {"ev": "dispatch", "task": "merge-w1", "id": "a3", "agent": "merger", "role": "merger", "model": "haiku"},
            {"ev": "report", "task": "merge-w1", "role": "merger", "verdict": "FAIL", "file": "m1.md"},
        )
        (fx,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((fx["task"], fx["agent"], fx["model"], fx["why"]), ("merge-fix-w1", "merger", "sonnet", "merger said FAIL"))
        self.assertIn("the merge of wave 1 failed (conflict or red tests): read m1.md.", fx["prompt"])
        self.log(
            {"ev": "dispatch", "task": "merge-fix-w1", "id": "a4", "agent": "dev", "role": "dev", "model": "sonnet"},
            {"ev": "report", "task": "merge-fix-w1", "role": "dev", "verdict": "DONE", "file": "f.md"},
        )
        (m2,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((m2["task"], m2["agent"]), ("merge-w1-2", "merger"))
        self.log(
            {"ev": "dispatch", "task": "merge-w1-2", "id": "a5", "agent": "merger", "role": "merger", "model": "haiku"},
            {"ev": "report", "task": "merge-w1-2", "role": "merger", "verdict": "DONE", "file": "m2.md"},
        )
        # merged -> index refresh recorded once, then done
        r = graph.decide(self.a)
        self.assertEqual(r["dispatches"][0]["task"], "index-refresh")  # the post-publish phase follows the per-wave refresh
        self.run_index.assert_called_once()
        logged = [json.loads(x) for x in (self.w / "orch.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual({k: logged[-1][k] for k in ("ev", "task", "rc")}, {"ev": "script", "task": "index-w1", "rc": 0})

    def test_blocked_merge_asks_lead_and_never_advances(self):
        self.tasks(flags="no-test no-audit no-retro no-push no-clean")
        self.passed()
        self.log(
            {"ev": "dispatch", "task": "merge-w1", "id": "a3", "agent": "merger", "role": "merger", "model": "haiku"},
            {"ev": "report", "task": "merge-w1", "role": "merger", "verdict": "BLOCKED", "file": "m.md"},
        )
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("merge-w1", r["message"])
        self.run_index.assert_not_called()

    def test_next_wave_never_starts_before_the_previous_merge_is_done(self):
        self.tasks(flags="no-test no-audit no-retro no-push no-clean", extra="| task-2 | b | dev | 1 | wave W2, haiku |", more="\nwave 2: task-2")
        self.passed()
        r = graph.decide(self.a)  # wave 1 is reviewed: its merge comes next, not the wave-2 dev
        self.assertEqual([d["task"] for d in r["dispatches"]], ["merge-w1"])
        self.log(
            {"ev": "dispatch", "task": "merge-w1", "id": "a3", "agent": "merger", "role": "merger", "model": "haiku"},
            {"ev": "report", "task": "merge-w1", "role": "merger", "verdict": "BLOCKED", "file": "m.md"},
        )
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")  # blocked merge: wave 2 still waits, so its worktree is never cut from an unmerged dev
        self.log(
            {"ev": "dispatch", "task": "merge-w1", "id": "a4", "agent": "merger", "role": "merger", "model": "haiku"},
            {"ev": "report", "task": "merge-w1", "role": "merger", "verdict": "DONE", "file": "m2.md"},
        )
        r = graph.decide(self.a)
        self.assertEqual([d["task"] for d in r["dispatches"]], ["task-2"])

    def test_done_task_with_unmerged_worktree_asks_lead(self):
        Path(self.d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False, "worktrees": True}), encoding="utf-8")
        self.tasks(flags="no-test no-audit no-retro no-index no-push no-clean")
        self.passed()
        with mock.patch.object(graph, "_merge_wave", return_value=None), mock.patch.object(graph, "job_info", return_value={"root": self.d}):
            r = graph.decide(self.a)
            self.assertEqual((r["action"], r["dispatches"][0]["task"]), ("dispatch", "merge-w1-late"))
            self.log(
                {"ev": "dispatch", "task": "merge-w1-late", "id": "a9", "agent": "script", "role": "script", "model": None},
                {"ev": "report", "task": "merge-w1-late", "role": "script", "verdict": "DONE", "file": "x.md"},
            )
            r = graph.decide(self.a)
            self.assertEqual(r["action"], "ask_main")  # late merge done, worktree still there: the old ask is the fallback
            self.assertIn("task-1", r["message"])
            self.assertIn("unmerged", r["message"])
            self.log({"ev": "skip", "task": "task-1"})
            self.assertNotIn("unmerged", graph.decide(self.a).get("message", ""))
        with mock.patch.object(graph, "_merge_wave", return_value=None), mock.patch.object(graph, "job_info", return_value=None):
            self.assertNotIn("unmerged", graph.decide(self.a).get("message", ""))

    def _job_repo(self, commit):
        """Real main repo (branch dev) with task-1's job worktree; `commit` adds a commit on the job branch. -> job root."""
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        main = os.path.join(self.d, "main")
        os.makedirs(main)
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"repo": "main", "branch_work": "dev", "gate": False, "testrun": False, "qualityrun": False, "worktrees": True}), encoding="utf-8"
        )

        def sh(*cmd):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)

        sh("-C", main, "init", "-q", "-b", "dev")
        Path(main, "f").write_text("x", encoding="utf-8")
        sh("-C", main, "add", "f")
        sh("-C", main, "commit", "-qm", "i")
        root = worktree.create(self.d, "s", "task-1")["root"]
        if commit:
            Path(root, "g").write_text("y", encoding="utf-8")
            sh("-C", root, "add", "g")
            sh("-C", root, "commit", "-qm", "task-1 work")
        return root

    def test_done_task_with_empty_job_branch_is_removed_not_blocking(self):
        root = self._job_repo(commit=False)
        self.tasks(flags="no-test no-audit no-retro no-index no-push no-clean")
        self.passed()
        with mock.patch.object(graph, "_merge_wave", return_value=None):
            r = graph.decide(self.a)
        self.assertNotIn("unmerged", r.get("message", ""))
        self.assertFalse(os.path.isdir(root))
        self.assertNotIn("task-1", worktree.read_manifest(self.d, "s"))

    def _squash_into_stage_branch(self):
        """What a merge with a red gate leaves: the job's content is on the stage branch (squash merge, no ancestry), the job branch and worktree stay."""
        main = os.path.join(self.d, "main")
        branch = worktree._parts(self.d, "s", "task-1")[1]
        for cmd in (["merge", "--squash", branch], ["commit", "-qm", "task-1 squashed"]):
            subprocess.run(["git", "-C", main, "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)

    def test_done_task_whose_content_is_in_the_stage_branch_is_removed_not_blocking(self):
        root = self._job_repo(commit=True)
        self._squash_into_stage_branch()
        self.tasks(flags="no-test no-audit no-retro no-index no-push no-clean")
        self.passed()
        with mock.patch.object(graph, "_merge_wave", return_value=None):
            r = graph.decide(self.a)
        self.assertNotIn("unmerged", r.get("message", ""))
        self.assertFalse(os.path.isdir(root))
        self.assertNotIn("task-1", worktree.read_manifest(self.d, "s"))

    def test_done_task_marked_merged_by_hand_is_removed_not_blocking(self):
        root = self._job_repo(commit=True)
        worktree.mark_merged(self.d, "s", "task-1")
        self.tasks(flags="no-test no-audit no-retro no-index no-push no-clean")
        self.passed()
        with mock.patch.object(graph, "_merge_wave", return_value=None), mock.patch.object(worktree, "is_merged", return_value=False):
            r = graph.decide(self.a)
        self.assertNotIn("unmerged", r.get("message", ""))
        self.assertFalse(os.path.isdir(root))

    def test_done_task_with_commits_on_job_branch_still_blocks(self):
        root = self._job_repo(commit=True)
        self.tasks(flags="no-test no-audit no-retro no-index no-push no-clean")
        self.passed()
        with mock.patch.object(graph, "_merge_wave", return_value=None):
            r = graph.decide(self.a)
        self.assertEqual((r["action"], r["dispatches"][0]["task"]), ("dispatch", "merge-w1-late"))  # not in the work branch yet: the late merge runs first
        self.assertTrue(os.path.isdir(root))

    def test_sonnet_merger_blocked_goes_to_lead(self):
        self.tasks(flags="no-test no-audit no-retro no-push no-clean")
        self.passed()
        self.log(
            {"ev": "dispatch", "task": "merge-w1", "id": "a3", "agent": "merger", "role": "merger", "model": "haiku"},
            {"ev": "report", "task": "merge-w1", "role": "merger", "verdict": "FAIL", "file": "m1.md"},
            {"ev": "dispatch", "task": "merge-fix-w1", "id": "a4", "agent": "merger", "role": "merger", "model": "sonnet"},
            {"ev": "report", "task": "merge-fix-w1", "role": "merger", "verdict": "BLOCKED", "file": "f.md"},
        )
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("merge-fix-w1", r["message"])

    def test_index_failure_only_warns(self):
        self.tasks(flags="no-test no-audit no-retro no-push no-clean")
        self.passed()
        self.log(
            {"ev": "dispatch", "task": "merge-w1", "id": "a3", "agent": "merger", "role": "merger", "model": "haiku"},
            {"ev": "report", "task": "merge-w1", "role": "merger", "verdict": "DONE", "file": "m.md"},
        )
        self.run_index.return_value = 3
        err = io.StringIO()
        with redirect_stderr(err):
            r = graph.decide(self.a)
        self.assertEqual(r["action"], "dispatch")
        self.assertEqual(err.getvalue(), "warning: index refresh after wave 1 exited 3\n")

    def test_retro_phases_then_done(self):
        self.tasks(flags="no-merge no-test no-audit no-index no-push no-clean")
        self.passed()
        (n,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((n["task"], n["agent"], n["model"]), ("retro-numbers", "script", None))
        self.assertIn("retro_numbers.py --verdict --orch .work/s/orch.jsonl --out .work/s/retro/retro-numbers.md", n["prompt"])
        self.log(
            {"ev": "dispatch", "task": "retro-numbers", "id": "a3", "agent": "script", "role": "script", "model": None},
            {"ev": "report", "task": "retro-numbers", "role": "script", "verdict": "FAIL", "file": "n.md"},
        )
        (dg,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((dg["task"], dg["agent"], dg["model"], dg["why"]), ("retro-digest", "ops", "haiku", "stage end: cost digest for the retro"))
        self.assertIn("Write .work/s/retro/retro-digest.md (max 25 lines", dg["prompt"])
        self.log(
            {"ev": "dispatch", "task": "retro-digest", "id": "a4", "agent": "ops", "role": "ops", "model": "haiku"},
            {"ev": "report", "task": "retro-digest", "role": "ops", "verdict": "BLOCKED", "file": "g.md"},
        )
        self.assertEqual(graph.decide(self.a), {"action": "done", "message": "all 1 task(s) reviewed PASS"})

    def test_done_message_variants(self):
        audit = [{"ev": "report", "task": "audit-code", "role": "auditor", "verdict": "DONE"}]
        flags = {"test": True, "audit": True}
        self.assertEqual(graph._done_message([1], flags, audit), "all 1 task(s) reviewed PASS; tests and audits clean")
        self.assertEqual(graph._done_message([1], flags, []), "all 1 task(s) reviewed PASS; tests clean")
        self.assertEqual(graph._done_message([1, 2], {"test": False, "audit": True}, audit), "all 2 task(s) reviewed PASS; audits clean")
        self.assertEqual(graph._done_message([1], {"test": False, "audit": True}, []), "all 1 task(s) reviewed PASS;  clean")
        self.assertEqual(graph._done_message([1], {"test": False, "audit": False}, audit), "all 1 task(s) reviewed PASS")


if __name__ == "__main__":
    unittest.main()
