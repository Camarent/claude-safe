import io
import json
import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("ORCH_NO_INDEX", "1")  # tests must not start background indexers
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs, cli_orch, graph, phases, steps, tasks, triggers, verdicts
from pipeline.util import read_jsonl
from tests.support.tmpdirs import tracked_mkdtemp

_INDEX_PATCH = mock.patch.object(graph, "run_index", return_value=0)  # tests never run the real indexer


def setUpModule():
    _INDEX_PATCH.start()


def tearDownModule():
    _INDEX_PATCH.stop()


TASKS = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |
| task-2 | b | dev(sonnet) | 1 | wave W2, sonnet |

## Graph
wave 1: task-1 | wave 2: task-2
flags: no-merge no-test no-audit no-retro
"""


class OrchTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        os.makedirs(f"{self.d}/.work/s/reports")
        Path(f"{self.d}/.work/s/TASKS.md").write_text(TASKS, encoding="utf-8")
        Path(f"{self.d}/pipeline.json").write_text('{"gate": false, "qualityrun": false, "testrun": false}', encoding="utf-8")

    def run_cmd(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli_orch.main([*[*args, "--stage", "s", "--dir", self.d][:1], "--stage", "s", "--dir", self.d, *list(args[1:])])
        return json.loads(out.getvalue())

    def nxt(self):
        r = self.run_cmd("next")
        if r["action"] == "dispatch":
            self.assertEqual(len(r["dispatches"]), 1)
            return {"action": "dispatch", **r["dispatches"][0]}
        return r

    def nxt_all(self):
        return self.run_cmd("next")

    def rep(self, i, text):
        return self.run_cmd("report", "--id", i, "--text", text)

    def test_the_same_ask_twice_in_a_row_is_logged_once(self):
        log = f"{self.d}/.work/s/orch.jsonl"
        evs: list = []
        for msg, want in (("a", 1), ("a", 1), ("b", 2), ("a", 3)):
            cli_orch._log_ask(log, evs, msg)
            evs = [json.loads(x) for x in open(log, encoding="utf-8")]
            self.assertEqual(len(evs), want, msg)

    def test_audit_report_materialized_other_dir(self):
        """Layout 2: repo folder below the workspace (worktree-style dir), not the temp root."""
        outer = self.d
        self.addCleanup(shutil.rmtree, outer, True)
        self.d = os.path.join(outer, "wt", "job")
        os.makedirs(f"{self.d}/.work/s/reports")
        shutil.copy(f"{outer}/.work/s/TASKS.md", f"{self.d}/.work/s/TASKS.md")
        self.test_audit_report_materialized()

    def test_audit_report_materialized(self):
        self.addCleanup(shutil.rmtree, self.d, True)
        log = f"{self.d}/.work/s/orch.jsonl"
        for i, task in (("x1", "audit-arch"), ("x2", "audit-sec-2")):
            with open(log, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps({"ev": "dispatch", "id": i, "task": task, "role": "arch-auditor" if task == "audit-arch" else "sec-auditor", "agent": "a"})
                    + "\n"
                )
        Path(f"{self.d}/.work/s/audits").mkdir()
        Path(f"{self.d}/.work/s/audits/audit-sec-2.md").write_text("keep", encoding="utf-8")
        self.rep("x1", "findings\nVERDICT: FIXES")
        self.rep("x2", "other\nVERDICT: CLEAN")
        self.assertIn("findings", Path(f"{self.d}/.work/s/audits/audit-arch.md").read_text(encoding="utf-8"))
        self.assertEqual(Path(f"{self.d}/.work/s/audits/audit-sec-2.md").read_text(encoding="utf-8"), "keep")

    def test_happy_path_two_tasks(self):
        a = self.nxt()
        self.assertEqual((a["agent"], a["model"]), ("dev", "haiku"))
        self.assertIn("task-1.md", a["prompt"])
        self.rep(a["id"], "task-1 changed, tests pass")
        a = self.nxt()
        self.assertEqual((a["agent"], a["model"]), ("reviewer", "haiku"))
        self.rep(a["id"], "ESCALATE: logic needs judgment")  # reviewer asks for a stronger one
        a = self.nxt()
        self.assertEqual((a["agent"], a["model"]), ("reviewer", "sonnet"))  # a Haiku ESCALATE goes straight to Sonnet
        self.assertNotIn("LOGIC " + "REVIEW", a["prompt"])

    def test_dev_and_reviewer_prompts_state_in_place_and_stage_base(self):
        class A:
            stage = "s"
            dir = self.d  # the temp project, never the real cwd project (its pipeline.json may pin branch_work)

        task = {"id": "task-1"}
        self.assertIn("in place on `stage/s`", briefs.prompt_for(A, task, "dev", None, "x1"))
        self.assertIn("commit before this stage's first task", briefs.prompt_for(A, task, "reviewer", None, "x2"))

    def test_reviewer_brief_states_driver_test_run(self):
        class A:
            stage = "s"
            dir = self.d

        info = {"branch": "job/t1", "base": "abc", "repos": {"r": "/w/r"}}
        with mock.patch("pipeline.briefs.job_info", return_value=info), mock.patch("pipeline.briefs.job_diff", return_value=""):
            with mock.patch("pipeline.briefs.testrun_on", return_value=True):
                self.assertIn("Driver test run:", briefs.prompt_for(A, {"id": "task-1"}, "reviewer", None, "x3"))
            with mock.patch("pipeline.briefs.testrun_on", return_value=False):
                self.assertNotIn("Driver test run:", briefs.prompt_for(A, {"id": "task-1"}, "reviewer", None, "x4"))

    def test_reviewer_brief_states_lint_left_count(self):
        class A:
            stage = "s"
            dir = self.d

        from audit import qualityrun

        text = "head\n\nrepo (eslint): 2 finding(s)\n  a.js:1\n\nrepo2 (clippy): 1 finding(s)\n  b.rs:3"
        with mock.patch("pipeline.briefs.qualityrun_on", return_value=True):
            with mock.patch.object(qualityrun, "run", return_value=("FAIL", text)):
                self.assertIn("Driver lint left: 3 finding(s)", briefs._lint_left(A, {"id": "task-1"}))
            with mock.patch.object(qualityrun, "run", return_value=("DONE", "clean")):
                self.assertEqual(briefs._lint_left(A, {"id": "task-1"}), "")
            with mock.patch.object(qualityrun, "run", side_effect=RuntimeError("x")):
                self.assertEqual(briefs._lint_left(A, {"id": "task-1"}), "")
        with mock.patch("pipeline.briefs.qualityrun_on", return_value=False):
            self.assertEqual(briefs._lint_left(A, {"id": "task-1"}), "")

    def test_dev_escalate_goes_to_sonnet_once(self):
        a = self.nxt()
        self.rep(a["id"], "ESCALATE: needs a decision")
        a = self.nxt()
        self.assertEqual((a["agent"], a["model"]), ("dev", "haiku"))
        self.rep(a["id"], "ESCALATE: needs a decision")
        a = self.nxt()
        self.assertEqual((a["agent"], a["model"]), ("dev", "sonnet"))
        self.assertNotIn("LOGIC " + "OK", a["prompt"])
        self.rep(a["id"], "ESCALATE: still unclear")
        self.assertEqual(self.nxt()["action"], "ask_main")

    def test_report_reads_file_not_text(self):
        a = self.nxt()
        self.assertIn("report-a1.md", a["prompt"])
        self.assertIn("REPORT a1", a["prompt"])
        Path(f"{self.d}/.work/s/reports/report-a1.md").write_text("changed, tests pass", encoding="utf-8")
        self.assertEqual(self.run_cmd("report", "--id", "a1")["verdict"], "DONE")  # no --text
        b = self.nxt()
        self.assertEqual(b["agent"], "reviewer")
        self.assertEqual(self.run_cmd("report", "--id", b["id"])["verdict"], "FAIL")  # agent wrote no file -> safe verdict

    def test_report_event_logs_why(self):
        def last_report():
            evs = [json.loads(x) for x in Path(f"{self.d}/.work/s/orch.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            return [e for e in evs if e["ev"] == "report"][-1]

        a = self.nxt()
        self.assertEqual(a["agent"], "dev")
        Path(f"{self.d}/.work/s/reports/report-a1.md").write_text("VERDICT: BLOCKED\nneeds file x outside the task", encoding="utf-8")
        self.run_cmd("report", "--id", "a1")
        self.assertEqual(last_report()["why"], "needs file x outside the task")
        self.setUp()  # fresh stage per case: a BLOCKED dev is re-dispatched, so the next dispatch differs
        self.nxt()
        Path(f"{self.d}/.work/s/reports/report-a1.md").write_text("ESCALATE: tried twice, tests stay red\nVERDICT: ESCALATE", encoding="utf-8")
        self.run_cmd("report", "--id", "a1")
        self.assertEqual(last_report()["why"], "tried twice, tests stay red")
        self.setUp()
        self.nxt()
        Path(f"{self.d}/.work/s/reports/report-a1.md").write_text("", encoding="utf-8")
        self.run_cmd("report", "--id", "a1")
        self.assertEqual(last_report()["why"], "")

    def test_text_never_overwrites_real_report(self):
        a = self.nxt()
        f = f"{self.d}/.work/s/reports/report-{a['id']}.md"
        Path(f).write_text("VERDICT: BLOCKED\nreal", encoding="utf-8")
        self.assertEqual(self.rep(a["id"], "REPORT a1")["verdict"], "BLOCKED")
        self.assertEqual(Path(f).read_text(encoding="utf-8"), "VERDICT: BLOCKED\nreal")

    def test_text_fills_missing_report(self):
        a = self.nxt()
        f = f"{self.d}/.work/s/reports/report-{a['id']}.md"
        self.rep(a["id"], "full text")
        self.assertEqual(Path(f).read_text(encoding="utf-8"), "full text")

    def test_unreported_dispatch_blocks(self):
        self.nxt()
        self.assertEqual(self.nxt()["action"], "ask_main")

    def test_unclear_verdict_retries_once_then_blocks(self):
        a = self.nxt()
        r = self.rep(a["id"], "hmm, something")
        self.assertEqual(r["verdict"], "BLOCKED")
        self.assertTrue(r.get("retry"))
        b = self.nxt()
        self.assertEqual((b["action"], b["agent"]), ("dispatch", "dev"))
        self.assertIn("VERDICT", b["prompt"])
        r = self.rep(b["id"], "hmm again")
        self.assertFalse(r.get("retry"))
        self.assertEqual(self.nxt()["action"], "ask_main")

    def test_retry_clears_pending_dispatch(self):
        self.nxt()
        self.assertEqual(self.nxt()["action"], "ask_main")
        self.run_cmd("retry", "--step", "task-1")
        self.assertEqual(self.nxt()["action"], "dispatch")

    def test_skip_names_the_log_it_wrote_and_fails_when_the_event_is_missing(self):
        r = self.run_cmd("skip", "--step", "task-1")
        self.assertEqual(r["recorded"], "skip")
        self.assertTrue(os.path.exists(r["log"]))
        self.assertEqual(json.loads(Path(r["log"]).read_text().splitlines()[-1])["ev"], "skip")
        with mock.patch.object(cli_orch, "append_event"), self.assertRaises(SystemExit):
            self.run_cmd("skip", "--step", "task-9")

    def test_manual_step_holds_its_wave_until_skipped(self):
        Path(f"{self.d}/.work/s/TASKS.md").write_text(
            TASKS.replace("wave 1: task-1 | wave 2: task-2", "wave 1: ops-1 | wave 2: task-1 | wave 3: task-2") + "\n- [ ] ops-1 manual: apply the forms\n",
            encoding="utf-8",
        )
        r = self.nxt()
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("ops-1", r["message"])
        self.assertIn("apply the forms", r["message"])
        self.assertEqual(self.nxt()["action"], "ask_main")  # still held
        self.run_cmd("skip", "--step", "ops-1")
        a = self.nxt()
        self.assertEqual((a["action"], a["agent"]), ("dispatch", "dev"))
        self.assertIn("task-1.md", a["prompt"])

    def test_pending_message_names_report_command(self):
        a = self.nxt()
        self.assertIn(f"report --id {a['id']}", self.nxt()["message"])

    def test_auto_retry_is_per_role(self):
        a = self.nxt()
        self.assertTrue(self.rep(a["id"], "hmm").get("retry"))
        rows = read_jsonl(os.path.join(self.d, ".work", "s", "metrics.jsonl"))
        self.assertEqual([(r["ev"], r["id"], r["reason"]) for r in rows if r["ev"] == "auto_retry"], [("auto_retry", a["id"], "no clear verdict")])
        b = self.nxt()
        self.rep(b["id"], "all green, tests pass")
        r = self.nxt()
        self.assertEqual(r["agent"], "reviewer")
        self.assertTrue(self.rep(r["id"], "dunno").get("retry"))

    def test_dev_auto_retry_keeps_reviewer_fail_rounds(self):
        a = self.nxt()
        self.rep(a["id"], "VERDICT: DONE")
        r = self.nxt()
        self.assertEqual(r["agent"], "reviewer")
        self.rep(r["id"], "VERDICT: FAIL")
        d = self.nxt()
        self.assertEqual(d["agent"], "dev")
        self.assertTrue(self.rep(d["id"], "dunno").get("retry"))  # unclear dev report: auto retry
        with open(f"{self.d}/.work/s/orch.jsonl", encoding="utf-8") as f:
            evs = [json.loads(line) for line in f]
        kept = steps.history(evs, "task-1")
        self.assertIn(("report", "reviewer", "FAIL"), [(e["ev"], e.get("role"), e.get("verdict")) for e in kept])
        self.assertEqual(steps._failed_rounds(kept), 1)

    def test_pre_approved_master_merge_skips_pause(self):
        class A:
            stage = "s"
            dir = self.d

        flags = {**tasks.parse_flags(f"{self.d}/.work/s/TASKS.md"), "audit": False, "merge": False, "test": False, "index": False}

        def pauses():
            return [st for g in phases.final_phases(A, flags, [], []) for st in g if st["id"] == "master-merged"]

        self.assertTrue(pauses())
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write("PRE-APPROVED: master merge\n")
        self.assertFalse(pauses())
        Path(f"{self.d}/.work/s/TASKS.md").write_text("PRE-APPROVED: master merge later maybe\n", encoding="utf-8")
        self.assertTrue(pauses())

    def test_ordering_and_done(self):
        order = [t["id"] for t in tasks.parse_tasks(f"{self.d}/.work/s/TASKS.md")]
        self.assertEqual(order, ["task-1", "task-2"])

    def test_sonnet_plan_row_dispatches_haiku_first(self):
        t = f"{self.d}/.work/s/TASKS.md"
        Path(t).write_text("- [ ] task-1 - a - owner: dev(sonnet) - max price: 1 - review: own, sonnet - depends: -\n", encoding="utf-8")
        r = self.nxt()
        self.assertEqual((r["action"], r["agent"], r["model"]), ("dispatch", "dev", "haiku"))

    def test_bullet_format_and_unparsed_guard(self):
        t = f"{self.d}/.work/s/TASKS.md"
        with open(t, "w", encoding="utf-8") as f:
            f.write(
                "- [ ] task-1 - a - owner: dev - max price: 1 - review: own, haiku - depends: -\n"
                "- [ ] task-2 \u2014 b \u2014 owner: dev(sonnet) \u2014 max price: 2 \u2014 review: own, sonnet \u2014 depends: task-1\n"
                "- wave 1: task-2 (x)\n"
            )
        got = [(x["id"], x["owner_sonnet"], x["review_sonnet"]) for x in tasks.parse_tasks(t)]
        self.assertEqual(got, [("task-2", False, False), ("task-1", False, False)])  # sonnet words in the plan are ignored
        Path(t).write_text("task-1 is mentioned in prose only\n", encoding="utf-8")
        self.assertEqual(self.nxt()["action"], "ask_main")

    def test_next_returns_with_strata_down(self):
        Path(f"{self.d}/.work/s/TASKS.md").write_text("", encoding="utf-8")  # empty task table
        env = {**os.environ, "ORCH_LEAD": "1", "STRATA_URL": "http://127.0.0.1:9"}
        p = subprocess.run(
            [
                sys.executable,
                "-I",
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orch.py"),
                "next",
                "--stage",
                "s",
                "--dir",
                self.d,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )  # TimeoutExpired fails the test
        self.assertNotIn("Traceback", p.stderr)
        self.assertTrue(p.stdout.strip().startswith("{"), p.stdout)
        json.loads(p.stdout.strip().splitlines()[-1])


class LeadRequestTest(OrchTest):
    def setUp(self):
        super().setUp()
        self.agents = tracked_mkdtemp()
        for n, m in (("dev", "haiku"), ("researcher", "sonnet"), ("publisher", "haiku"), ("architect", "opus")):
            Path(f"{self.agents}/{n}.md").write_text(f"---\nname: {n}\ndescription: {n} agent\nmodel: {m}\n---\nbody\n", encoding="utf-8")
        os.environ["ORCH_AGENTS_DIR"] = self.agents
        os.makedirs(f"{self.d}/.work/s/requests")

    def tearDown(self):
        for k in ("ORCH_AGENTS_DIR", "ORCH_MAX_OPUS", "ORCH_GATED"):
            os.environ.pop(k, None)

    def request(self, name, **kw):
        Path(f"{self.d}/.work/s/brief-{name}.md").write_text("do the thing", encoding="utf-8")
        body = {"agent": "researcher", "model": "haiku", "brief": f".work/s/brief-{name}.md"}
        body.update(kw)
        Path(f"{self.d}/.work/s/requests/{name}.json").write_text(json.dumps(body), encoding="utf-8")

    def events(self):
        with open(f"{self.d}/.work/s/orch.jsonl", encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_any_agent_by_file_and_report_roundtrip(self):
        self.request("01-web")
        a = self.nxt()
        self.assertEqual((a["action"], a["agent"], a["model"]), ("dispatch", "researcher", "haiku"))
        self.assertIn("brief-01-web.md", a["prompt"])
        self.assertIn("REPORT a1", a["prompt"])
        self.assertTrue(os.path.exists(f"{self.d}/.work/s/requests/done/01-web.json"))  # consumed
        Path(f"{self.d}/.work/s/reports/report-a1.md").write_text("found it", encoding="utf-8")
        self.assertEqual(self.run_cmd("report", "--id", "a1")["verdict"], "REPORTED")  # no judge call for non-graph agents
        self.assertEqual(self.nxt()["agent"], "dev")  # back to the Graph

    def test_rejections_are_logged_not_run(self):
        self.request("01-bad", agent="hacker")
        self.request("02-out", brief="../../etc/passwd")
        self.request("03-model", model="gpt")
        a = self.nxt()
        self.assertEqual(a["agent"], "dev")  # all three skipped, Graph continues
        why = [e["why"] for e in self.events() if e["ev"] == "reject"]
        self.assertEqual(len(why), 3)
        self.assertTrue(any("unknown agent" in w for w in why))

    def test_rejected_file_is_logged_once_until_edited(self):
        from types import SimpleNamespace

        from pipeline import requests

        self.request("01-bad", agent="hacker")
        a, w = SimpleNamespace(dir=self.d, stage="s"), f"{self.d}/.work/s"
        log = f"{w}/orch.jsonl"

        def rejects():
            return len([e for e in self.events() if e["ev"] == "reject"])

        self.assertIsNone(requests.take_request(a, w, log))
        self.assertIsNone(requests.take_request(a, w, log))
        self.assertEqual(rejects(), 1)  # not re-parsed, not logged again
        os.utime(f"{w}/requests/01-bad.json", (1, 1))  # edited by the lead: looked at again
        self.assertIsNone(requests.take_request(a, w, log))
        self.assertEqual(rejects(), 2)

    def test_outward_facing_agent_needs_the_owner(self):
        self.request("01-pub", agent="publisher")
        self.assertEqual(self.nxt()["action"], "ask_main")

    def test_opus_budget(self):
        os.environ["ORCH_MAX_OPUS"] = "0"
        self.request("01-arch", agent="architect", model=None)  # agent default is opus
        self.assertEqual(self.nxt()["agent"], "dev")
        self.assertTrue(any("opus" in e["why"] for e in self.events() if e["ev"] == "reject"))

    def test_verdict_line_and_unclear_report(self):
        self.assertEqual(verdicts.verdict("dev", "VERDICT: BLOCKED\nall tests pass"), ("BLOCKED", "report line"))
        self.assertEqual(verdicts.verdict("reviewer", "wrong 0, risky 0, nit 0\nVERDICT: ESCALATE")[0], "PASS")  # nothing found: no second paid review
        self.assertEqual(verdicts.verdict("reviewer", "wrong 0, risky 1, nit 0\nVERDICT: ESCALATE")[0], "ESCALATE")
        self.assertEqual(verdicts.verdict("reviewer", "x\nVERDICT: PASS\n")[0], "PASS")
        self.assertEqual(verdicts.verdict("reviewer", "looks fine to me")[0], "FAIL")  # unclear -> safe
        self.assertEqual(verdicts.verdict("dev", "VERDICT: PASS")[0], "BLOCKED")  # not allowed for dev -> falls through

    def test_dev_heuristic_matches_whole_words_only(self):
        self.assertEqual(verdicts.verdict("dev", "Rotated the token file, not sure it worked.")[0], "BLOCKED")  # 'ok' inside 'token'
        self.assertEqual(verdicts.verdict("dev", "passport config touched, unclear")[0], "BLOCKED")  # 'pass' inside 'passport'
        self.assertEqual(verdicts.verdict("dev", "All tests passed.")[0], "DONE")
        self.assertEqual(verdicts.verdict("dev", "Suite is green. OK")[0], "DONE")
        self.assertEqual(verdicts.verdict("dev", "result: ok")[0], "BLOCKED")  # OK is case-sensitive


class UiLineTest(unittest.TestCase):
    def flags(self, txt):

        d = tracked_mkdtemp()
        t = os.path.join(d, "TASKS.md")
        Path(t).write_text(txt, encoding="utf-8")
        return tasks.parse_flags(t)

    def test_ui_yes_enables_ux(self):
        self.assertTrue(self.flags("UI: yes\nflags: docs\n")["ui"])

    def test_ui_no_keeps_ux_off(self):
        self.assertFalse(self.flags("UI: no\n")["ui"])


class ParallelTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        os.makedirs(f"{self.d}/.work/s/reports")
        Path(f"{self.d}/pipeline.json").write_text('{"gate": false, "qualityrun": false, "testrun": false}', encoding="utf-8")
        with open(f"{self.d}/.work/s/TASKS.md", "w", encoding="utf-8") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n"
                "| task-1 | a | dev | 1 | wave 1, haiku |\n| task-2 | b | dev | 1 | wave 1, haiku |\n| task-3 | c | dev | 1 | wave 2, haiku |\n\n"
                "## Graph\nwave 1: task-1, task-2 | wave 2: task-3\nflags: no-merge no-test no-audit no-retro\n"
            )

    def go(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli_orch.main([args[0], "--stage", "s", "--dir", self.d, *args[1:]])
        return json.loads(out.getvalue())

    def finish(self, i, text):
        Path(f"{self.d}/.work/s/reports/report-{i}.md").write_text(text, encoding="utf-8")
        self.go("report", "--id", i)

    def test_wave_dispatches_together_and_next_wave_waits(self):
        r = self.go("next")
        self.assertEqual([(x["id"], x["agent"]) for x in r["dispatches"]], [("a1", "dev"), ("a2", "dev")])
        self.assertIn("report-a1.md", r["dispatches"][0]["prompt"])
        self.assertIn("report-a2.md", r["dispatches"][1]["prompt"])
        self.finish("a1", "VERDICT: DONE")
        self.finish("a2", "VERDICT: DONE")
        r = self.go("next")  # both reviewed in parallel
        self.assertEqual([x["agent"] for x in r["dispatches"]], ["reviewer", "reviewer"])
        self.finish("a3", "VERDICT: PASS")
        self.finish("a4", "VERDICT: FAIL\nwrong: x")
        r = self.go("next")  # task-2 reworked; task-3 (wave 2) must wait
        self.assertEqual([x["agent"] for x in r["dispatches"]], ["dev"])
        self.assertIn("task-2.md", r["dispatches"][0]["prompt"])
        self.assertEqual(r["dispatches"][0]["id"], "a5")  # ids stay unique when task-1 already passed
        self.assertEqual(r["dispatches"][0]["model"], "sonnet")  # one FAIL: the rework dev is Sonnet, the reviewer stays Haiku
        self.finish("a5", "VERDICT: DONE")
        r = self.go("next")
        self.assertEqual(
            (r["dispatches"][0]["agent"], r["dispatches"][0]["model"]), ("reviewer", "haiku")
        )  # the reviewer moves up only by itself, not because the dev did
        self.finish("a6", "VERDICT: FAIL\nwrong: y")
        r = self.go("next")  # second FAIL: one extra dev round with the verbatim findings
        self.assertEqual([x["agent"] for x in r["dispatches"]], ["dev"])
        self.assertIn("reviews-all-task-2", r["dispatches"][0]["prompt"])

    def test_unreported_member_of_a_batch_blocks(self):
        self.go("next")
        self.finish("a1", "VERDICT: DONE")
        self.assertEqual(self.go("next")["action"], "ask_main")


class TriggerRuleT(unittest.TestCase):
    def test_architect_is_trigger_only_on_opus(self):
        self.assertFalse(triggers.is_trigger({"agent": "architect", "model": None}))
        self.assertTrue(triggers.is_trigger({"agent": "architect", "model": "opus"}))
        self.assertTrue(triggers.is_trigger({"agent": "sec-auditor", "model": None}))


if __name__ == "__main__":
    unittest.main()
