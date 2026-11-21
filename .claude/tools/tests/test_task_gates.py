"""Task gates (on by default with gates/each and worktrees): the driver runs the full gate set once per dev DONE; red goes back to the dev, never to pre-review or the reviewer."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import taskgates
from pipeline import briefs, steps

TASK = {"id": "task-1", "owner_sonnet": False, "review_sonnet": False}
GATE_WHY = "task gates: full gate set in the job worktree; FAIL goes back to the dev"


def ev(kind, **kw):
    return {"ev": kind, "task": "task-1", **kw}


def dev(i, model="haiku", free=False):
    return ev("dispatch", id=i, agent="dev", role="dev", model=model, why=None, free=free)


def done(i):
    return ev("report", id=i, role="dev", verdict="DONE", file="d.md")


def gate(i):
    return ev("dispatch", id=i, agent="script", role="script", model=None, why=GATE_WHY)


def gate_rep(i, verdict, file="g.md"):
    return ev("report", id=i, role="script", verdict=verdict, file=file)


class _SeqBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.a = SimpleNamespace(stage="s", dir=self.d)
        self.cfg()

    def cfg(self, **kw):
        base = {"worktrees": True, "gate": False, "testrun": False, "qualityrun": False, "prereview": True, "gates": [{"name": "lint", "cmd": "true"}]}
        Path(self.d, "pipeline.json").write_text(json.dumps({**base, **kw}), encoding="utf-8")

    def nxt(self, mine, task=TASK):
        self.a.orch_events = mine
        return steps.next_step(self.a, task, mine, "a9")

    def who(self, r):
        return (r["action"], r.get("role"), r.get("model"))


class GateSequenceTest(_SeqBase):
    def test_gate_step_follows_done_and_green_test_run(self):
        r = self.nxt([dev("a1"), done("a1")])
        self.assertEqual((r["role"], r["why"]), ("script", GATE_WHY))
        self.assertIn("audit/taskgates.py --stage s --task task-1", r["prompt"])
        self.cfg(testrun=True)
        r = self.nxt([dev("a1"), done("a1")])
        self.assertIn("testrun.py", r["prompt"])  # the Test lines first
        test = ev("dispatch", id="a2", agent="script", role="script", model=None, why="test run (x)")
        r = self.nxt([dev("a1"), done("a1"), test, gate_rep("a2", "DONE")])
        self.assertEqual(r["why"], GATE_WHY)

    def test_no_gate_step_without_gates_or_worktrees_or_after_escalate(self):
        self.cfg(gates=[])  # no gates and no each: nothing to run
        self.assertEqual(self.nxt([dev("a1"), done("a1")])["why"], "local pre-review (lp review); FAIL goes back to the dev")
        self.cfg(worktrees=False)  # no job worktree: the dev runs the gates itself
        self.assertNotEqual(self.nxt([dev("a1"), done("a1")]).get("why"), GATE_WHY)
        self.cfg()
        esc = ev("report", id="a1", role="dev", verdict="ESCALATE", file="d.md")
        self.assertEqual(self.who(self.nxt([dev("a1"), esc])), ("dispatch", "dev", "haiku"))

    def test_green_gates_go_to_prereview_then_reviewer(self):
        mine = [dev("a1"), done("a1"), gate("a2"), gate_rep("a2", "DONE")]
        self.assertIn("prereview.py", self.nxt(mine)["prompt"])
        self.cfg(prereview=False)
        self.assertEqual(self.who(self.nxt(mine)), ("dispatch", "reviewer", "haiku"))

    def test_red_goes_back_to_dev_with_output_and_skips_review(self):
        r = self.nxt([dev("a1"), done("a1"), gate("a2"), gate_rep("a2", "FAIL", "g1.md")])
        self.assertEqual((self.who(r), r["free"]), (("dispatch", "dev", "haiku"), True))
        self.assertIn("Rework: fix the findings in g1.md first.", r["prompt"])

    def test_ladder_two_free_rounds_then_sonnet_then_lead(self):
        f1, f2, f3 = (str(Path(self.d, f"g{i}.md")) for i in (1, 2, 3))
        for i, f in enumerate((f1, f2, f3), 1):
            Path(f).write_text(f"VERDICT: FAIL\nred {i}\n", encoding="utf-8")
        m = [dev("a1"), done("a1"), gate("a2"), gate_rep("a2", "FAIL", f1)]
        m += [dev("a3", free=True), done("a3"), gate("a4"), gate_rep("a4", "FAIL", f2)]
        r = self.nxt(m)
        self.assertEqual((self.who(r), r["free"]), (("dispatch", "dev", "haiku"), True))  # second free round
        m += [dev("a5", free=True), done("a5"), gate("a6"), gate_rep("a6", "FAIL", f3)]
        r = self.nxt(m)
        self.assertEqual((self.who(r), r["free"]), (("dispatch", "dev", "sonnet"), False))  # third red: Sonnet, counts as a try
        allf = r["prompt"].split("fix the findings in ")[1].split(" first")[0]
        text = Path(allf).read_text(encoding="utf-8")
        self.assertTrue(all(f"red {i}" in text for i in (1, 2, 3)))
        m += [dev("a9", "sonnet"), done("a9"), gate("a10"), gate_rep("a10", "FAIL", f3)]
        self.assertEqual(self.nxt(m)["action"], "ask_main")

    def test_a_rework_without_new_dev_done_does_not_rerun_gates(self):
        mine = [dev("a1"), done("a1"), gate("a2"), gate_rep("a2", "DONE")]
        self.assertNotEqual(self.nxt(mine).get("why"), GATE_WHY)

    def test_blocked_gates_ask_the_lead(self):
        self.assertEqual(self.nxt([dev("a1"), done("a1"), gate("a2"), gate_rep("a2", "BLOCKED")])["action"], "ask_main")

    def test_brief_sentence_depends_on_gates_and_worktrees(self):
        self.assertIn("Do not run the full suite: the driver runs the full gate set after you finish", briefs.gates_note(self.a))
        self.assertNotIn("Gates:", briefs.gates_note(self.a))
        self.assertIn("run only the build part of that gate before any test that loads the artifact", briefs.gates_note(self.a))
        self.assertIn("lint: `true`", briefs.gates_note(self.a))  # the gate name and command are named
        self.cfg(worktrees=False)
        self.assertIn("one `Gates:` line", briefs.gates_note(self.a))
        self.cfg(gates=[])
        self.assertEqual(briefs.gates_note(self.a), "")
        self.cfg(gates=[], each=[{"name": "e", "cmd": "true"}])  # each alone still means the driver runs them
        self.assertTrue(steps.task_gates_on(self.a))

    def test_dev_done_without_gates_line_is_fine_with_driver_gates(self):
        rep = Path(self.d, "d.md")
        rep.write_text("VERDICT: DONE\n", encoding="utf-8")
        e = ev("report", id="a1", role="dev", verdict="DONE", file=str(rep))
        self.cfg(prereview=False)
        r = self.nxt([dev("a1"), e, gate("a2"), gate_rep("a2", "DONE")])
        self.assertEqual(r["agent"], "reviewer")


class TaskGatesScriptTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"worktrees": True, "gate_flake_rerun": False, "gates": [{"name": "lint", "cmd": "mylint"}]}), encoding="utf-8"
        )
        self.ran: list = []
        self.base_rc = 0  # result on the stage base (the main checkout, self.d)
        self.base_ok = True  # the main checkout is a fair base (clean, on the stage branch)

    def runner(self, cmd, **kw):
        self.ran.append(kw["cwd"])
        return SimpleNamespace(returncode=self.rc if kw["cwd"] == "/job" else self.base_rc, stdout="boom\n", stderr="")

    def run_it(self, rc=0, sha="aaaa"):
        self.rc = rc
        patches = [
            mock.patch.object(taskgates.testrun, "workdir", return_value="/job"),
            mock.patch.object(taskgates, "content_sha", return_value=sha),
            mock.patch.object(taskgates, "no_change", return_value=False),
            mock.patch.object(taskgates, "_base_problem", side_effect=lambda *a, **k: "" if self.base_ok else "the main checkout has uncommitted changes"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return taskgates.run("s", "task-1", self.d, runner=self.runner)

    def test_runs_in_job_worktree_and_skips_unchanged_content(self):
        v, text = self.run_it()
        self.assertEqual((v, self.ran), ("DONE", ["/job"]))
        v, text = self.run_it()
        self.assertIn("unchanged", text)
        self.assertEqual(len(self.ran), 1)  # not run again
        v, _ = self.run_it(sha="bbbb")
        self.assertEqual((v, len(self.ran)), ("DONE", 2))  # changed content runs again

    def test_red_run_reports_gate_and_is_not_remembered(self):
        v, text = self.run_it(rc=1)
        self.assertEqual(v, "FAIL")
        self.assertIn("gate lint: FAILED", text)
        v, _ = self.run_it(rc=1)
        self.assertEqual(self.ran.count("/job"), 2)

    def test_red_gate_that_is_red_on_the_stage_base_is_not_the_tasks_failure(self):
        self.base_rc = 1
        v, text = self.run_it(rc=1)
        self.assertEqual(v, "DONE", text)
        self.assertIn("red on the stage base too", text)
        self.assertEqual(self.ran, ["/job", self.d])  # the red command ran once more, on the base

    def test_report_names_why_a_red_gate_was_not_cleared(self):
        self.base_ok = False
        self.base_rc = 1
        _v, text = self.run_it(rc=1)
        self.assertIn("not cleared against the stage base: the main checkout has uncommitted changes", text)
        self.base_ok = True
        self.base_rc = 0  # green on the base: the failure is the task's
        _v, text = self.run_it(rc=1, sha="s2")
        self.assertIn("green on the stage base", text)
        _v, text = self.run_it(rc=124, sha="s3")
        self.assertIn("exit 124 (timeout or cannot run) is never cleared", text)

    def set_cfg(self, **kw):
        Path(self.d, "pipeline.json").write_text(json.dumps({"worktrees": True, "gates": [{"name": "lint", "cmd": "mylint"}], **kw}), encoding="utf-8")

    def test_red_that_is_green_on_an_unchanged_rerun_is_a_flake_not_the_tasks_failure(self):
        self.set_cfg()
        outputs = iter([(1, "FAILED tests/test_a.py::test_x - boom\n"), (0, "ok\n")])

        def runner(cmd, **kw):
            self.ran.append(kw["cwd"])
            rc, out = next(outputs) if kw["cwd"] == "/job" else (0, "ok")
            return SimpleNamespace(returncode=rc, stdout=out, stderr="")

        self.rc = 1
        with (
            mock.patch.object(taskgates.testrun, "workdir", return_value="/job"),
            mock.patch.object(taskgates, "content_sha", return_value="aaaa"),
            mock.patch.object(taskgates, "no_change", return_value=False),
            mock.patch.object(taskgates, "_base_problem", return_value=""),
        ):
            v, text = taskgates.run("s", "task-1", self.d, runner=runner)
        self.assertEqual(v, "DONE", text)
        self.assertIn("base-flake: tests/test_a.py::test_x", text)
        self.assertEqual(self.ran.count("/job"), 2)  # once more, unchanged

    def test_red_on_both_runs_stays_the_tasks_failure_and_the_rerun_is_one(self):
        self.set_cfg()
        v, text = self.run_it(rc=1)
        self.assertEqual(v, "FAIL", text)
        self.assertNotIn("base-flake", text)
        self.assertEqual(self.ran.count("/job"), 2)

    def test_rerun_is_off_by_setting_and_never_for_timeouts(self):
        v, _ = self.run_it(rc=1)  # setUp config: gate_flake_rerun false
        self.assertEqual((v, self.ran.count("/job")), ("FAIL", 1))
        self.set_cfg()
        self.ran.clear()
        v, _ = self.run_it(rc=124, sha="s9")
        self.assertEqual((v, self.ran.count("/job")), ("FAIL", 1))

    def test_base_red_line_names_the_failing_tests(self):
        self.set_cfg(gate_flake_rerun=False)
        self.base_rc = 1
        _v, text = self.run_it(rc=1)
        self.assertIn("red on the stage base too", text)
        self.assertIn("base-flake: mylint", text)  # no test id in the output: the command is named

    def test_command_missing_on_the_base_is_named(self):
        with mock.patch.object(taskgates.final_check, "commands", side_effect=lambda cfg, repo: ["mylint"] if repo == "/job" else []):
            _v, text = self.run_it(rc=1)
        self.assertIn("the stage base has no such command", text)

    def test_a_write_through_a_nested_repo_link_fails_before_any_gate_runs(self):
        self.rc = 0
        with (
            mock.patch.object(taskgates.testrun, "workdir", return_value="/job"),
            mock.patch.object(taskgates.testrun, "outside_writes", return_value="FAIL: path outside this task's worktree: x"),
        ):
            v, text = taskgates.run("s", "task-1", self.d, runner=self.runner)
        self.assertEqual((v, getattr(self, "ran", [])), ("FAIL", []))
        self.assertIn("path outside", text)

    def test_unchanged_job_worktree_skips_the_gates(self):
        self.rc = 1
        with (
            mock.patch.object(taskgates.testrun, "workdir", return_value="/job"),
            mock.patch.object(taskgates, "no_change", return_value=True),
        ):
            v, text = taskgates.run("s", "task-1", self.d, runner=self.runner)
        self.assertEqual((v, getattr(self, "ran", [])), ("DONE", []))
        self.assertIn("no change against its base", text)

    def test_no_change_needs_base_commit_and_a_clean_tree_in_every_repo(self):
        parts = [("", self.d, "/job"), ("workspace/c", "/main-c", "/job/c")]
        heads = {"/job": "aa", "/main": "aa", self.d: "aa", "/job/c": "bb", "/main-c": "bb"}
        git = lambda w, *a: heads.get(w, "")  # noqa: E731
        dirty: list = []
        with (
            mock.patch.object(taskgates.wt, "live_job", return_value={"base": "stage/s"}),
            mock.patch.object(taskgates.wt, "_parts", return_value=({}, "job", parts)),
            mock.patch.object(taskgates.wt, "_dirty", side_effect=lambda w, r: dirty),
        ):
            self.assertTrue(taskgates.no_change(self.d, "s", "task-1", git))
            heads["/job/c"] = "cc"  # the nested worktree has a commit
            self.assertFalse(taskgates.no_change(self.d, "s", "task-1", git))
            heads["/job/c"] = "bb"
            dirty.append(" M f")
            self.assertFalse(taskgates.no_change(self.d, "s", "task-1", git))
        with mock.patch.object(taskgates.wt, "live_job", return_value=None):
            self.assertFalse(taskgates.no_change(self.d, "s", "task-1", git))
        with mock.patch.object(taskgates.wt, "live_job", side_effect=RuntimeError("git timed out")):  # a git that cannot answer: run the gates
            self.assertFalse(taskgates.no_change(self.d, "s", "task-1", git))
        heads["/job/c"] = "bb"
        dirty.clear()
        with (
            mock.patch.object(taskgates.wt, "live_job", return_value={"base": "stage/s"}),
            mock.patch.object(taskgates.wt, "_parts", return_value=({}, "job", parts)),
            mock.patch.object(taskgates.wt, "_dirty", return_value=[]),
        ):
            self.assertFalse(
                taskgates.no_change(self.d, "s", "task-1", lambda w, *a: None if a[0] == "status" else heads.get(w, ""))
            )  # git cannot say: not clean

    def test_dirty_or_other_branch_base_clears_nothing(self):
        self.base_ok = False
        self.base_rc = 1
        v, _ = self.run_it(rc=1)
        self.assertEqual((v, self.ran), ("FAIL", ["/job"]))

    def test_timeout_or_cannot_run_is_never_cleared(self):
        for rc in (124, 127):
            self.ran.clear()
            self.base_rc = rc
            v, _ = self.run_it(rc=rc, sha=f"s{rc}")
            self.assertEqual(v, "FAIL", rc)

    def test_command_the_base_does_not_have_is_not_cleared(self):
        with mock.patch.object(taskgates.final_check, "commands", side_effect=lambda cfg, repo: ["mylint"] if repo == "/job" else []):
            self.base_rc = 1
            v, _ = self.run_it(rc=1)
        self.assertEqual((v, self.ran), ("FAIL", ["/job"]))

    def test_base_problem_needs_the_stage_branch_and_a_clean_tree(self):
        calls = {"branch": "dev", "status": ""}

        def git(root, *a):
            return calls[a[0] if a[0] != "branch" else "branch"]

        cfg = {"branch_work": "dev"}
        self.assertEqual(taskgates._base_problem(cfg, self.d, "s", git=git), "")
        calls["status"] = " M f"
        self.assertTrue(taskgates._base_problem(cfg, self.d, "s", git=git))
        calls.update(status="", branch="stage/s")
        self.assertTrue(taskgates._base_problem(cfg, self.d, "s", git=git))

    def test_base_problem_with_real_git(self):  # gitx output keeps its trailing newline: the branch name must still match
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)

        def sh(*a):
            subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)

        sh("init", "-q", "-b", "dev")
        Path(repo, "f").write_text("x", encoding="utf-8")
        sh("add", "f")
        sh("commit", "-qm", "i")
        cfg = {"branch_work": "dev"}
        self.assertEqual(taskgates._base_problem(cfg, repo, "s"), "")
        Path(repo, "f").write_text("y", encoding="utf-8")
        self.assertTrue(taskgates._base_problem(cfg, repo, "s"))  # dirty
        sh("checkout", "-q", "--", "f")
        sh("checkout", "-q", "-b", "other")
        self.assertTrue(taskgates._base_problem(cfg, repo, "s"))  # not the work branch

    def test_green_run_does_not_touch_the_base(self):
        self.run_it(rc=0)
        self.assertEqual(self.ran, ["/job"])

    def test_gate_base_check_off_keeps_every_red(self):
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"worktrees": True, "gate_base_check": False, "gate_flake_rerun": False, "gates": [{"name": "lint", "cmd": "mylint"}]}), encoding="utf-8"
        )
        self.base_rc = 1
        v, _ = self.run_it(rc=1)
        self.assertEqual((v, self.ran), ("FAIL", ["/job"]))

    def test_only_the_gate_that_is_red_on_the_base_is_cleared(self):
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"worktrees": True, "gates": [{"name": "lint", "cmd": "mylint"}, {"name": "audit", "cmd": "myaudit"}]}), encoding="utf-8"
        )

        def runner(cmd, **kw):
            self.ran.append((cmd, kw["cwd"]))
            return SimpleNamespace(returncode=1 if (kw["cwd"] == "/job" or "myaudit" in str(cmd)) else 0, stdout="boom\n", stderr="")

        self.rc = 1
        with (
            mock.patch.object(taskgates.testrun, "workdir", return_value="/job"),
            mock.patch.object(taskgates, "content_sha", return_value="cccc"),
            mock.patch.object(taskgates, "no_change", return_value=False),
            mock.patch.object(taskgates, "_base_problem", return_value=""),
        ):
            v, text = taskgates.run("s", "task-1", self.d, runner=runner)
        self.assertEqual(v, "FAIL", text)
        self.assertIn("gate lint: FAILED", text)
        self.assertIn("gate audit: red on the stage base too", text)

    def test_each_gate_command_is_logged_with_seconds_and_the_base_red_flag(self):
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"worktrees": True, "gates": [{"name": "lint", "cmd": "mylint"}, {"name": "audit", "cmd": "myaudit"}]}), encoding="utf-8"
        )

        def runner(cmd, **kw):
            return SimpleNamespace(returncode=1 if (kw["cwd"] == "/job" or "myaudit" in str(cmd)) else 0, stdout="x\n", stderr="")

        os.makedirs(f"{self.d}/.work/s")
        with (
            mock.patch.object(taskgates.testrun, "workdir", return_value="/job"),
            mock.patch.object(taskgates, "content_sha", return_value="dddd"),
            mock.patch.object(taskgates, "no_change", return_value=False),
            mock.patch.object(taskgates, "_base_problem", return_value=""),
        ):
            taskgates.run("s", "task-1", self.d, runner=runner)
        recs = [json.loads(x) for x in open(f"{self.d}/.work/s/metrics.jsonl", encoding="utf-8")]
        self.assertEqual(
            [(r["ev"], r["kind"], r["task"], r["gate"], r["rc"]) for r in recs],
            [("gate_cmd", "task", "task-1", "lint", 1), ("gate_cmd", "task", "task-1", "audit", 0)],  # red on the base too: cleared, rc 0,
        )
        self.assertTrue(all(isinstance(r["seconds"], (int, float)) for r in recs))
        self.assertEqual([bool(r.get("base_red")) for r in recs], [False, True])

    def test_a_metrics_write_failure_does_not_stop_the_gates(self):
        os.makedirs(f"{self.d}/.work/s")
        os.makedirs(f"{self.d}/.work/s/metrics.jsonl")  # a directory: the append fails
        v, _ = self.run_it()
        self.assertEqual(v, "DONE")

    def test_no_job_worktree_is_blocked(self):
        with mock.patch.object(taskgates.testrun, "workdir", return_value=None):
            self.assertEqual(taskgates.run("s", "task-1", self.d)[0], "BLOCKED")


class StraightToSonnetTest(_SeqBase):
    """PLAN GAP and the first-run no-edit ESCALATE skip the Haiku retry and the local judge gate."""

    def esc(self, text):
        f = Path(self.d, "esc.md")
        f.write_text(text, encoding="utf-8")
        return ev("report", id="a1", role="dev", verdict="ESCALATE", file=str(f))

    def test_plan_gap_goes_straight_to_sonnet_even_with_judge_on(self):
        self.cfg(gate=True)
        r = self.nxt([dev("a1"), self.esc("VERDICT: ESCALATE\nESCALATE: PLAN GAP: no unit in records, core.js:12\n")])
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))  # no judge script in between
        self.assertIn("Rework: fix the findings in " + str(Path(self.d, "esc.md")), r["prompt"])

    def test_plan_gap_from_sonnet_asks_the_lead_with_the_text(self):
        e = self.esc("ESCALATE: PLAN GAP: no unit in records, core.js:12\n")
        r = self.nxt([dev("a1", "sonnet"), e])
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("PLAN GAP: no unit in records", r["message"])

    def test_plain_escalate_keeps_the_haiku_retry(self):
        r = self.nxt([dev("a1"), self.esc("ESCALATE: tests fail\n")])
        self.assertEqual(self.who(r), ("dispatch", "dev", "haiku"))

    def test_first_run_no_edit_goes_to_sonnet_without_judge(self):
        self.cfg(gate=True)
        e = self.esc("ESCALATE: too big\n")
        with (
            mock.patch.object(steps, "job_heads", return_value="=abc"),
            mock.patch.object(steps, "job_diff", return_value=""),
            mock.patch.object(steps, "job_dirty", return_value=[]),
        ):
            self.assertEqual(self.who(self.nxt([dev("a1"), e])), ("dispatch", "dev", "sonnet"))
            second = [dev("a1"), e, dev("a2"), self.esc("ESCALATE: again\n")]
            self.assertEqual(self.nxt(second)["role"], "script")  # not a first run: the judge gate runs first
        with mock.patch.object(steps, "job_heads", return_value="=abc"), mock.patch.object(steps, "job_diff", return_value="diff"):
            self.assertEqual(self.who(self.nxt([dev("a1"), e])), ("dispatch", "dev", "haiku"))  # something was edited: today's ladder


if __name__ == "__main__":
    unittest.main()
