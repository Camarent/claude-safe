"""pipeline.graph: worktree-mode script merge ladder, dependency-mode partial merge, manual steps, index refresh warning, PR steps."""

import io
import json
import os
import shlex
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import graph, pr_mode
from pipeline.configuration import MERGE_CONFLICT_MARK, MERGE_RED_MARK
from tests.support.tmpdirs import tracked_mkdtemp

TASKS = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |
| task-2 | b | dev | 1 | wave W2, haiku |

## Graph
wave 1: task-1
wave 2: task-2
flags: {flags}
PRE-APPROVED: master merge
"""
NOFLAGS = "no-test no-audit no-retro no-push no-clean"


def ev(kind, task, **kw):
    return {"ev": kind, "task": task, **kw}


def done(task, i, role="dev", verdict="DONE", file="f.md"):
    return [ev("dispatch", task, id=i, agent=role, role=role, model="haiku"), ev("report", task, id=i, role=role, verdict=verdict, file=file)]


def passed(task, i):
    return done(task, i) + done(task, i + "r", "reviewer", "PASS")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.w = Path(self.d, ".work", "s")
        self.w.mkdir(parents=True)
        self.a = SimpleNamespace(stage="s", dir=self.d)
        for name, val in (
            ("has_origin", True),
            ("pr_active", False),
            ("gh_down", False),
            ("direct_flow", True),
            ("sweep_active", False),
        ):  # legacy flow: an origin, no PR; tests never call gh
            pa = mock.patch.object(pr_mode, name, return_value=val)
            pa.start()
            self.addCleanup(pa.stop)
        self.evs = []
        self.cfg()

    def cfg(self, **kw):
        Path(self.d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False, "worktrees": True, **kw}), encoding="utf-8")

    def tasks(self, flags=NOFLAGS):
        (self.w / "TASKS.md").write_text(TASKS.format(flags=flags), encoding="utf-8")

    def log(self, *events):
        self.evs.extend(events)
        (self.w / "orch.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.evs), encoding="utf-8")

    def patch(self, *a, **kw):
        p = mock.patch.object(*a, **kw)
        m = p.start()
        self.addCleanup(p.stop)
        return m


class ScriptMerge(Base):
    def setUp(self):
        super().setUp()
        self.patch(graph, "_has_job_branches", return_value=True)
        self.patch(graph, "job_info", return_value={"branch": "job/t", "repos": {"main": "/p"}})
        self.patch(graph, "_unmerged_done", return_value=[])  # the merge ladder is under test here, not the done-but-unmerged check (LateMerge covers it)
        self.patch(graph, "run_index", return_value=0)

    def one(self):
        (d,) = graph.decide(self.a)["dispatches"]
        return d

    def test_ladder_script_fail_sonnet_fix_script_again(self):
        self.tasks(NOFLAGS + " no-index")
        self.log(*passed("task-1", "a1"))
        d = self.one()
        self.assertEqual((d["task"], d["agent"], d["why"]), ("merge-w1", "script", "merge by script"))
        self.assertIn("merge_wave.py", d["prompt"])
        self.assertIn(f"--dir {os.path.abspath(self.d)} --stage s --tasks task-1", d["prompt"])
        self.log(*done("merge-w1", "a3", "script", "FAIL", "m1.md"))
        d = self.one()
        self.assertEqual((d["task"], d["agent"], d["model"], d["why"]), ("merge-fix-w1", "merger", "sonnet", "script merge said FAIL"))
        self.assertIn("CONFLICT-RESOLVE MODE", d["prompt"])
        self.assertIn("read m1.md", d["prompt"])
        self.assertIn("Read then Edit", d["prompt"])  # B3: the resolver path is named, no improvised helper scripts
        self.assertIn("never write helper scripts", d["prompt"])
        self.log(*done("merge-fix-w1", "a4", "merger"))
        d = self.one()
        self.assertEqual((d["task"], d["agent"], d["why"]), ("merge-w1-2", "script", "merge by script, after the fix"))

    def test_red_gate_dispatches_fix_gate_dev_then_remerges(self):
        self.tasks(NOFLAGS + " no-index")
        rep = self.w / "m1.md"
        rep.write_text(f"VERDICT: FAIL\n{MERGE_RED_MARK} (branches kept)\nvalidate: warnings\n", encoding="utf-8")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", str(rep)))
        d = self.one()
        self.assertEqual((d["task"], d["agent"], d["model"]), ("fix-gate-w1", "dev", "haiku"))
        self.assertIn(str(rep), d["prompt"])
        self.assertNotIn("merge-fix-w1", [e["task"] for e in self.evs])
        self.log(*done("fix-gate-w1", "a4", "dev"))
        d = self.one()
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-g1", "script"))

    def test_red_gate_second_fix_is_sonnet_then_lead(self):
        self.tasks(NOFLAGS + " no-index")
        rep = self.w / "m1.md"
        rep.write_text(f"VERDICT: FAIL\n{MERGE_RED_MARK} (branches kept)\n", encoding="utf-8")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", str(rep)), *done("fix-gate-w1", "a4", "dev"))
        self.log(*done("merge-w1-g1", "a5", "script", "FAIL", str(rep)))
        d = self.one()
        self.assertEqual((d["task"], d["model"]), ("fix-gate-w1-2", "sonnet"))
        self.log(*done("fix-gate-w1-2", "a6", "dev"), *done("merge-w1-g2", "a7", "script", "FAIL", str(rep)))
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("merge-w1", r["message"])

    def red_merge(self, *more):
        self.tasks(NOFLAGS + " no-index")
        rep = self.w / "m1.md"
        rep.write_text(f"VERDICT: FAIL\n{MERGE_RED_MARK} (branches kept)\n", encoding="utf-8")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", str(rep)), *more)

    def test_green_remerge_after_fix_lets_the_wave_go_on(self):
        self.red_merge(*done("fix-gate-w1", "a4", "dev"), *done("merge-w1-g1", "a5", "script", "DONE"))
        self.assertNotEqual(graph.decide(self.a).get("action"), "ask_main")

    def test_red_gate_fix_blocked_goes_to_sonnet(self):
        self.red_merge(*done("fix-gate-w1", "a4", "dev", "BLOCKED"))
        d = self.one()
        self.assertEqual((d["task"], d["model"]), ("fix-gate-w1-2", "sonnet"))

    def test_conflict_on_remerge_after_fix_goes_to_the_merger(self):
        rep2 = self.w / "m2.md"
        rep2.write_text(f"VERDICT: FAIL\ntask-1: {MERGE_CONFLICT_MARK}\n", encoding="utf-8")
        self.red_merge(*done("fix-gate-w1", "a4", "dev"), *done("merge-w1-g1", "a5", "script", "FAIL", str(rep2)))
        d = self.one()
        self.assertEqual((d["task"], d["agent"], d["model"]), ("merge-fix-w1", "merger", "sonnet"))
        self.assertIn("read " + str(rep2), d["prompt"])
        self.log(*done("merge-fix-w1", "a6", "merger"))
        self.assertEqual(self.one()["task"], "merge-w1-2")

    def test_red_gate_step_list_fix_then_sonnet_then_lead(self):
        # exact order for a red gate with no conflict: dev Haiku -> script g1 -> dev Sonnet -> script g2 -> lead
        self.red_merge()
        seq = []
        for i, verdict in enumerate(("DONE", "FAIL", "DONE", "FAIL"), 4):
            d = self.one()
            seq.append((d["task"], d["agent"], d["model"]))
            self.log(*done(d["task"], f"s{i}", d["agent"], verdict, str(self.w / "m1.md")))
        self.assertEqual(
            seq,
            [
                ("fix-gate-w1", "dev", "haiku"),
                ("merge-w1-g1", "script", None),
                ("fix-gate-w1-2", "dev", "sonnet"),
                ("merge-w1-g2", "script", None),
            ],
        )
        self.assertEqual(graph.decide(self.a)["action"], "ask_main")
        self.assertNotIn("merge-fix-w1", [e["task"] for e in self.evs])

    def test_conflict_resolved_then_red_remerge_goes_to_fix_gate(self):
        # wave 1 shape: conflict -> merge-fix-w1 (Sonnet) -> merge-w1-2 red -> fix-gate dev, not the lead
        self.tasks(NOFLAGS + " no-index")
        rep1 = self.w / "m1.md"
        rep1.write_text(f"VERDICT: FAIL\ntask-1: {MERGE_CONFLICT_MARK}\n", encoding="utf-8")
        rep3 = self.w / "m3.md"
        rep3.write_text(f"VERDICT: FAIL\n{MERGE_RED_MARK} (branches kept)\n", encoding="utf-8")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", str(rep1)))
        self.assertEqual((self.one()["task"], self.one()["agent"]), ("merge-fix-w1", "merger"))
        self.log(*done("merge-fix-w1", "a4", "merger"), *done("merge-w1-2", "a5", "script", "FAIL", str(rep3)))
        d = self.one()
        self.assertEqual((d["task"], d["agent"], d["model"]), ("fix-gate-w1", "dev", "haiku"))
        self.log(*done("fix-gate-w1", "a6", "dev"), *done("merge-w1-g1", "a7", "script", "DONE"))
        self.assertNotEqual(graph.decide(self.a).get("action"), "ask_main")

    def test_non_red_fail_after_merge_fix_goes_to_lead_not_fix_gate(self):
        rep2 = self.w / "m2.md"
        rep2.write_text(f"VERDICT: FAIL\ntask-1: {MERGE_CONFLICT_MARK}\n", encoding="utf-8")
        self.red_merge(*done("fix-gate-w1", "a4", "dev"), *done("merge-w1-g1", "a5", "script", "FAIL", str(rep2)))
        self.log(*done("merge-fix-w1", "a6", "merger"), *done("merge-w1-2", "a7", "script", "FAIL", str(rep2)))
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertNotIn("fix-gate-w1-2", [e["task"] for e in self.evs])

    def test_conflict_only_setting_sends_non_conflict_fail_to_lead(self):
        self.cfg(merge_fix_conflict_only=True)
        self.tasks(NOFLAGS + " no-index")
        rep = self.w / "m1.md"
        rep.write_text("VERDICT: FAIL\ntask-1: git exploded\n", encoding="utf-8")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", str(rep)))
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("no conflict", r["message"])
        self.assertNotIn("merge-fix-w1", [e["task"] for e in self.evs])

    def test_conflict_only_setting_still_fixes_a_real_conflict(self):
        self.cfg(merge_fix_conflict_only=True)
        self.tasks(NOFLAGS + " no-index")
        rep = self.w / "m1.md"
        rep.write_text(f"VERDICT: FAIL\ntask-1: {MERGE_CONFLICT_MARK}, main checkout reset\n", encoding="utf-8")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", str(rep)))
        self.assertEqual(self.one()["task"], "merge-fix-w1")

    def test_without_the_setting_any_non_red_fail_still_goes_to_the_merger(self):
        self.tasks(NOFLAGS + " no-index")
        rep = self.w / "m1.md"
        rep.write_text("VERDICT: FAIL\ntask-1: git exploded\n", encoding="utf-8")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", str(rep)))
        self.assertEqual(self.one()["task"], "merge-fix-w1")

    def test_merge_fix_skipped_when_branches_already_merged_by_hand(self):
        self.tasks(NOFLAGS + " no-index")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", "m1.md"))
        self.patch(graph, "_already_merged", return_value=True)
        d = self.one()
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-2", "script"))

    def test_already_merged_task_skips_merger_even_with_conflict_only_setting(self):
        self.cfg(merge_fix_conflict_only=True)
        self.tasks(NOFLAGS + " no-index")
        rep = self.w / "m1.md"
        rep.write_text("VERDICT: FAIL\ntask-1: git exploded\n", encoding="utf-8")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", str(rep)))
        self.patch(graph, "_already_merged", return_value=True)
        d = self.one()
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-2", "script"))

    def test_script_done_skips_the_fix_and_refreshes_index_once(self):
        self.tasks()
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "DONE"))
        r = graph.decide(self.a)
        self.assertEqual(r["dispatches"][0]["task"], "task-2")
        self.assertEqual(sum(1 for e in self.evs if e["task"] == "merge-fix-w1"), 0)

    def test_script_blocked_asks_lead(self):
        self.tasks()
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "BLOCKED", "m.md"))
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")

    def test_manual_step_waits_for_skip(self):
        (self.w / "TASKS.md").write_text(
            "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave W1, haiku |\n"
            "- [ ] ops-1 manual: rotate the key\n\n## Graph\nwave 1: task-1\nwave 2: ops-1\nflags: no-merge no-test no-audit no-retro no-index no-push no-clean no-release\n",
            encoding="utf-8",
        )
        self.log(*passed("task-1", "a1"))
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("manual step ops-1: rotate the key", r["message"])
        self.assertIn("`orch.py skip --step ops-1` (stage s)", r["message"])
        self.log(ev("skip", "ops-1"))
        self.assertEqual(graph.decide(self.a)["action"], "done")


class IndexAndPr(Base):
    def test_run_index_failure_returns_1_and_warns(self):
        err = io.StringIO()
        with mock.patch.object(graph, "run_py", side_effect=OSError("nope")), redirect_stderr(err):
            self.assertEqual(graph.run_index(self.a), 1)
        self.assertIn("index refresh failed: nope", err.getvalue())
        self.assertIn("swallowed in graph.run_index: OSError: nope", err.getvalue())
        with mock.patch.object(graph, "run_py", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(graph.run_index(self.a), 0)

    def test_refresh_index_warns_on_nonzero(self):
        err = io.StringIO()
        with mock.patch.object(graph, "run_index", return_value=3), redirect_stderr(err):
            graph._refresh_index(self.a, 2)
        self.assertIn("index refresh after wave 2 exited 3", err.getvalue())
        logged = [json.loads(x) for x in (self.w / "orch.jsonl").read_text().splitlines()]
        self.assertEqual((logged[-1]["task"], logged[-1]["rc"]), ("index-w2", 3))

    def test_ask_all_joins_messages(self):
        self.assertEqual(graph.ask_all([{"message": "a"}, {"message": "b"}]), {"action": "ask_main", "message": "a\nb"})

    def test_pr_open_and_push_steps_when_active(self):
        self.cfg(worktrees=False)
        self.tasks(NOFLAGS + " no-index")
        p1 = mock.patch.object(graph.pr_mode, "pr_active", return_value=True)
        p2 = mock.patch.object(graph.pr_mode, "sweep_active", return_value=True)
        p3 = mock.patch.object(graph, "_has_job_branches", return_value=False)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        d = graph.decide(self.a)["dispatches"][0]
        self.assertEqual((d["task"], d["agent"]), ("deps-sweep", "script"))
        self.assertIn("pr_steps.py sweep", d["prompt"])
        self.log(*done("deps-sweep", "a1", "script"))
        d = graph.decide(self.a)["dispatches"][0]
        self.assertNotEqual(d["task"], "pr-open")  # the PR opens at the first push, not at stage start
        self.log(*passed("task-1", "a3"))
        d = graph.decide(self.a)["dispatches"][0]
        self.assertEqual(d["task"], "pr-push-w1")
        self.assertIn("pr_steps.py push", d["prompt"])


class PartialMerge(Base):
    def test_stuck_task_does_not_stop_merge_of_the_finished_one(self):
        text = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |
| task-2 | b | dev | 1 | wave W1, haiku |
| task-3 | c | dev | 1 | wave W2, haiku |
Depends: task-2

## Graph
wave 1: task-1, task-2
wave 2: task-3
flags: no-test no-audit no-retro no-push no-clean no-index
PRE-APPROVED: master merge
"""
        (self.w / "TASKS.md").write_text(text, encoding="utf-8")
        self.patch(graph, "job_info", return_value={"branch": "b", "repos": {}})
        self.log(*passed("task-1", "a1"), *done("task-2", "a3", "dev", "BLOCKED", "x.md"))
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "dispatch")
        (d,) = r["dispatches"]
        self.assertEqual(d["task"], "merge-w1-p1")
        self.assertIn("--tasks task-1", d["prompt"])
        self.assertNotIn("task-2", d["prompt"].split("--tasks")[1])
        # merged: the stuck task and its dependent are reported together
        self.log(*done("merge-w1-p1", "a5", "script"))
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("held back by them, not started: task-3", r["message"])

    def test_conflict_in_the_partial_merge_gets_a_merger_fix_before_the_lead(self):
        text = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |
| task-2 | b | dev | 1 | wave W1, haiku |
| task-3 | c | dev | 1 | wave W2, haiku |
Depends: task-2

## Graph
wave 1: task-1, task-2
wave 2: task-3
flags: no-test no-audit no-retro no-push no-clean no-index
"""
        (self.w / "TASKS.md").write_text(text, encoding="utf-8")
        self.patch(graph, "job_info", return_value={"branch": "b", "repos": {}})
        self.log(*passed("task-1", "a1"), *done("task-2", "a3", "dev", "BLOCKED", "x.md"), *done("merge-w1-p1", "a5", "script", "FAIL"))
        d = graph.decide(self.a)["dispatches"][0]
        self.assertEqual(
            (d["task"], d["agent"], d["model"]), ("merge-fix-w1-p1", "merger", "sonnet")
        )  # suffixed: the later full-wave merge keeps its own merge-fix-w1
        self.log(*done("merge-fix-w1-p1", "a6", "merger"))
        d = graph.decide(self.a)["dispatches"][0]
        self.assertEqual(d["task"], "merge-w1-p1-2")
        self.log(*done("merge-w1-p1-2", "a7", "script"))
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("held back by them, not started: task-3", r["message"])


class JobBranches(Base):
    def git(self, *a, cwd=None):
        subprocess.run(["git", "-C", cwd or self.d, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)

    def test_only_work_branch_means_nothing_to_merge_extra_branch_means_merge(self):
        self.cfg(branch_work="dev", branch_release="dev")
        self.git("init", "-q", "-b", "dev")
        Path(self.d, "f").write_text("x")
        self.git("add", "f")
        self.git("commit", "-qm", "i")
        self.assertFalse(graph._has_job_branches(self.a))
        self.git("branch", "job/s-task-1")
        self.assertTrue(graph._has_job_branches(self.a))

    def test_no_git_repo_or_unrunnable_git_lets_the_merger_look(self):
        self.cfg(branch_work="dev", branch_release="dev")
        self.assertTrue(graph._has_job_branches(self.a))
        self.git("init", "-q", "-b", "dev")
        with mock.patch.object(graph.subprocess, "run", side_effect=OSError):
            self.assertTrue(graph._has_job_branches(self.a))

    def test_nested_repo_with_extra_branch_counts(self):
        self.cfg(branch_work="dev", branch_release="dev")
        sub = Path(self.d, "course")
        sub.mkdir()
        self.git("init", "-q", "-b", "dev", cwd=str(sub))
        (sub / "f").write_text("x")
        self.git("add", "f", cwd=str(sub))
        self.git("commit", "-qm", "i", cwd=str(sub))
        self.assertFalse(graph._has_job_branches(self.a))
        self.git("branch", "job/x", cwd=str(sub))
        self.assertTrue(graph._has_job_branches(self.a))


class FinishPhase(Base):
    def test_feature_retro_step_runs_in_the_finish_phase(self):
        self.tasks("no-merge no-test no-audit no-index no-push no-clean")
        self.log(*passed("task-1", "a1"), *passed("task-2", "a3"), *done("retro-numbers", "a5", "script", "DONE"))
        with mock.patch.object(graph, "is_finish_phase", return_value=True), mock.patch.object(graph, "earlier_phase_tasks_mds", return_value=[]):
            (d,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("feature-retro", "script"))
        self.assertIn("feature_retro.py --feature-dir .work/", d["prompt"])
        self.assertIn("--out .work/s/feature-problems.md", d["prompt"])
        self.assertNotIn("--lead-log", d["prompt"])  # no lead log known: nothing passed

    def _feature_retro_prompt(self):
        self.tasks("no-merge no-test no-audit no-index no-push no-clean")
        self.log(*passed("task-1", "a1"), *passed("task-2", "a3"), *done("retro-numbers", "a5", "script", "DONE"))
        with mock.patch.object(graph, "is_finish_phase", return_value=True), mock.patch.object(graph, "earlier_phase_tasks_mds", return_value=[]):
            (d,) = graph.decide(self.a)["dispatches"]
        return d["prompt"]

    def test_feature_retro_gets_lead_log_from_env(self):
        from pipeline.configuration import ENV_LEAD_LOG

        with mock.patch.dict(os.environ, {ENV_LEAD_LOG: f"/x/a b.jsonl{os.pathsep}/x/c.jsonl"}):
            prompt = self._feature_retro_prompt()
        self.assertIn("--lead-log '/x/a b.jsonl' --lead-log /x/c.jsonl", prompt)

    def test_feature_retro_gets_lead_log_from_pipeline_json(self):
        with open(os.path.join(self.d, "pipeline.json"), "w") as fh:
            json.dump({"lead_log": ["/x/lead.jsonl"]}, fh)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_LEAD_LOG", None)
            prompt = self._feature_retro_prompt()
        self.assertIn("--lead-log /x/lead.jsonl", prompt)


LATE_TASKS = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |
| task-2 | b | dev | 1 | wave W1, haiku |

## Graph
wave 1: task-1, task-2
flags: no-test no-audit no-retro no-push no-clean no-index
PRE-APPROVED: master merge
"""


class LateMerge(Base):
    """Done tasks retried after the wave's merge pass: the driver merges them by script (merge-w{N}-late) before it asks the lead."""

    def setUp(self):
        super().setUp()
        self.gone: set = set()
        self.patch(graph, "job_info", side_effect=lambda a, tid, create=False: None if tid in self.gone else {"branch": "job/t", "repos": {"main": "/p"}})
        self.patch(graph.worktree, "empty_job", return_value=False)
        self.patch(graph, "run_index", return_value=0)
        (self.w / "TASKS.md").write_text(LATE_TASKS, encoding="utf-8")
        # wave merged, then both tasks retried and DONE again: their worktrees are still there
        self.log(*passed("task-1", "a1"), *passed("task-2", "a2"), *done("merge-w1", "a3", "script"), *done("task-1", "a4"), *done("task-2", "a5"))

    def decide(self):
        return graph.decide(self.a)

    def test_retried_done_tasks_get_a_late_script_merge_then_the_stage_goes_on(self):
        r = self.decide()
        self.assertEqual(r["action"], "dispatch")
        (d,) = r["dispatches"]
        self.assertEqual((d["task"], d["agent"], d["why"]), ("merge-w1-late", "script", "merge the retried tasks by script"))
        self.assertIn(f"--dir {os.path.abspath(self.d)} --stage s --tasks task-1,task-2", d["prompt"])
        self.gone |= {"task-1", "task-2"}
        self.log(*done("merge-w1-late", "a6", "script"))
        r = self.decide()
        self.assertEqual(r["action"], "done")
        self.assertNotIn("unmerged", r["message"])

    def test_red_gate_in_late_merge_goes_to_fix_gate_dev(self):
        rep = self.w / "m1.md"
        rep.write_text(f"VERDICT: FAIL\n{MERGE_RED_MARK} (branches kept)\n", encoding="utf-8")
        self.log(*done("merge-w1-late", "a6", "script", "FAIL", str(rep)))
        (d,) = self.decide()["dispatches"]
        self.assertEqual((d["task"], d["agent"], d["model"]), ("fix-gate-w1-late", "dev", "haiku"))
        self.assertIn(str(rep), d["prompt"])
        self.log(*done("fix-gate-w1-late", "a7", "dev"))
        (d,) = self.decide()["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-late-g1", "script"))

    def test_conflict_in_late_merge_gets_a_merger_fix_then_script_again(self):
        rep = self.w / "m1.md"
        rep.write_text(f"VERDICT: FAIL\n{MERGE_CONFLICT_MARK}\n", encoding="utf-8")
        self.log(*done("merge-w1-late", "a6", "script", "FAIL", str(rep)))
        (d,) = self.decide()["dispatches"]
        self.assertEqual((d["task"], d["agent"], d["model"]), ("merge-fix-w1-late", "merger", "sonnet"))
        self.log(*done("merge-fix-w1-late", "a7", "merger"))
        (d,) = self.decide()["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-late-2", "script"))

    def test_late_merge_fail_without_merger_asks_the_lead_with_the_late_step_name(self):
        self.cfg(worktrees=True, merge_fix_tries=0)
        rep = self.w / "m1.md"
        rep.write_text("VERDICT: FAIL\nno conflict\n", encoding="utf-8")
        self.log(*done("merge-w1-late", "a6", "script", "FAIL", str(rep)))
        r = self.decide()
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("merge-w1-late", r["message"])

    def test_late_merge_done_but_worktree_still_there_keeps_the_old_ask(self):
        self.log(*done("merge-w1-late", "a6", "script"))
        r = self.decide()
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("still have an unmerged worktree branch", r["message"])
        self.assertIn("task-1,task-2", r["message"])


LATE_TWO_WAVES = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |
| task-3 | b (added to wave 1 after its merge) | dev | 1 | wave W1, haiku |
| task-2 | c | dev | 1 | wave W2, haiku |

## Graph
wave 1: task-1, task-3
wave 2: task-2
flags: no-test no-audit no-retro no-push no-clean no-index
PRE-APPROVED: master merge
"""


class LateTaskBeforeNextWave(Base):
    """A task added to (or retried in) a wave AFTER its merge step ran is merged before the next wave dispatches: a later task must never branch from a base that lacks a done task's commit."""

    def setUp(self):
        super().setUp()
        self.unmerged: set = {"task-3"}
        self.patch(graph, "job_info", side_effect=lambda a, tid, create=False: {"branch": "job/t", "repos": {"main": "/p"}} if tid in self.unmerged else None)
        self.patch(graph.worktree, "empty_job", return_value=False)
        self.patch(graph, "run_index", return_value=0)
        (self.w / "TASKS.md").write_text(LATE_TWO_WAVES, encoding="utf-8")
        # wave 1 was merged (task-1 taken), then task-3 was added to wave 1 and finished: its branch is not in the stage branch
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a2", "script"), *passed("task-3", "a3"))

    def test_the_next_wave_waits_for_a_late_merge_of_the_done_task(self):
        (d,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-late", "script"))  # not task-2 of wave 2
        self.assertIn("--tasks task-3", d["prompt"])
        self.unmerged.discard("task-3")
        self.log(*done("merge-w1-late", "a4", "script"))
        (d,) = graph.decide(self.a)["dispatches"]
        self.assertEqual(d["task"], "task-2")  # now the next wave starts, from a base that holds task-3

    def late_done(self, step, rid, tasks, verdict="DONE"):
        """A late-merge dispatch that records the tasks it covers, and its report."""
        return [
            ev("dispatch", step, id=rid, agent="script", role="script", model=None, tasks=tasks),
            ev("report", step, id=rid, role="script", verdict=verdict, file="f.md"),
        ]

    def add_task_to_wave_1(self, tid):
        text = (self.w / "TASKS.md").read_text(encoding="utf-8")
        text = text.replace("| task-2 |", f"| {tid} | added later | dev | 1 | wave W1, haiku |\n| task-2 |", 1)
        (self.w / "TASKS.md").write_text(text.replace("wave 1: ", f"wave 1: {tid}, ", 1), encoding="utf-8")

    def test_a_task_finished_after_the_first_late_merge_gets_its_own_late_step(self):
        self.log(*self.late_done("merge-w1-late", "a4", ["task-3"]))
        self.unmerged.discard("task-3")
        self.unmerged.add("task-4")
        self.add_task_to_wave_1("task-4")
        self.log(*passed("task-4", "a5"))
        (d,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-late2", "script"))
        self.assertIn("--tasks task-4", d["prompt"])
        self.assertEqual(d["tasks"], ["task-4"])  # logged with the dispatch
        # compare argument tokens, not the line: the worktree path of task-3 also contains "task-3"
        self.assertNotIn("task-3", shlex.split(d["prompt"]))

    def test_a_failed_late_ladder_that_ended_green_does_not_block_the_next_batch(self):
        self.log(
            *self.late_done("merge-w1-late", "a4", ["task-3"], "FAIL"), *done("merge-fix-w1-late", "a5", "merger"), *done("merge-w1-late-2", "a6", "script")
        )
        self.unmerged.discard("task-3")
        self.unmerged.add("task-4")
        self.add_task_to_wave_1("task-4")
        self.log(*passed("task-4", "a7"))
        (d,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-late2", "script"))  # not skipped, not an ask

    def test_a_running_ladder_continues_with_its_own_step_ids(self):
        self.log(*self.late_done("merge-w1-late", "a4", ["task-3"], "FAIL"))
        rep = self.w / "m.md"
        rep.write_text(f"VERDICT: FAIL\n{MERGE_CONFLICT_MARK}\n", encoding="utf-8")
        self.log(ev("report", "merge-w1-late", id="a4", role="script", verdict="FAIL", file=str(rep)))
        (d,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("merge-fix-w1-late", "merger"))  # the ladder of batch 1, not a new batch

    def test_a_task_retried_and_finished_again_after_its_late_merge_is_merged_again(self):
        self.log(*self.late_done("merge-w1-late", "a4", ["task-3"]))
        self.log(*passed("task-3", "a9"))  # retried: a new DONE after the batch (the worktree is back)
        (d,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-late2", "script"))
        self.assertIn("--tasks task-3", d["prompt"])

    def test_a_retry_of_a_late_step_after_a_finished_ladder_runs_it_again(self):
        self.log(
            *self.late_done("merge-w1-late", "a4", ["task-3"], "FAIL"), *done("merge-fix-w1-late", "a5", "merger"), *done("merge-w1-late-2", "a6", "script")
        )
        # `orch.py retry --step merge-w1-late` writes a retry event for the step and for its ladder (cli_orch._reset_fix_chain)
        self.log(*[ev("retry", sid, note="retry of merge-w1-late") for sid in ("merge-fix-w1-late", "merge-w1-late-2", "merge-w1-late")])
        (d,) = graph.decide(self.a)["dispatches"]
        self.assertEqual((d["task"], d["agent"]), ("merge-w1-late", "script"))  # not an ask: the lead's retry did something

    def test_the_same_task_that_stays_unmerged_after_its_batch_is_not_merged_again(self):
        self.log(*self.late_done("merge-w1-late", "a4", ["task-3"]))
        self.assertEqual(graph.decide(self.a)["action"], "ask_main")

    def test_a_task_still_there_after_its_late_merge_asks_the_lead_before_the_next_wave(self):
        self.log(*done("merge-w1-late", "a4", "script"))  # worktree of task-3 still exists
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("still have an unmerged worktree branch", r["message"])
        self.assertIn("task-3", r["message"])


if __name__ == "__main__":
    unittest.main()
