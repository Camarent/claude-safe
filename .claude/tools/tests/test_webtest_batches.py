"""Final browser check in batches (no 4-item cap), the re-check of unverified items, the UX flows file and the UX CLEAN-with-UNVERIFIED rule."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import phases, plan_check, ux_flows, verdicts
from pipeline.configuration import WEBTEST_BATCH
from tests.support.tmpdirs import tracked_mkdtemp


def rep(sid, verdict, kind="report"):
    return {"ev": kind, "task": sid, "verdict": verdict, "file": f"/r/{sid}-{verdict}.md"}


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.work = Path(self.d, ".work", "s")
        self.work.mkdir(parents=True)
        self.a = SimpleNamespace(stage="s", dir=self.d, feature=None)
        self.cfg = {}

    def tasks(self, n, ui=True):
        out = []
        for i in range(1, n + 1):
            path = f"web/a{i}.js" if ui else f"docs/a{i}.md"
            (self.work / f"task-{i}.md").write_text(f"# T\nPredicted paths: {path}\n", encoding="utf-8")
            out.append({"id": f"task-{i}", "wave": 1})
        return out

    def steps_of(self, tasks, evs):
        return [s for group in phases._web_test_phases(self.a, self.cfg, tasks, evs) for s in group]

    def test_batches_cover_every_ui_task(self):
        tasks = self.tasks(WEBTEST_BATCH * 2 + 1)
        batches = phases._ui_batches(self.a, self.cfg, tasks)
        self.assertEqual([len(b) for b in batches], [WEBTEST_BATCH, WEBTEST_BATCH, 1])
        self.assertEqual(sum(batches, []), [t["id"] for t in tasks])

    def test_non_ui_tasks_are_left_out_and_none_means_all(self):
        tasks = [*self.tasks(2), {"id": "task-9", "wave": 1}]
        (self.work / "task-9.md").write_text("Predicted paths: docs/x.md\n", encoding="utf-8")
        self.assertEqual(phases._ui_batches(self.a, self.cfg, tasks), [["task-1", "task-2"]])
        self.assertEqual(phases._ui_batches(self.a, self.cfg, self.tasks(2, ui=False)), [["task-1", "task-2"]])

    def test_first_step_only_until_it_reported(self):
        first = self.steps_of(self.tasks(WEBTEST_BATCH + 1), [])[0]
        self.assertEqual(first["id"], "final-webtest")
        self.assertNotIn("max 4 checklist items", first["prompt"])
        self.assertEqual(first["model"], "sonnet")

    def test_all_pass_ends_with_stop(self):
        evs = [rep("final-webtest", "PASS"), rep("final-webtest-b2", "PASS")]
        ids = [s["id"] for s in self.steps_of(self.tasks(WEBTEST_BATCH + 1), evs)]
        self.assertEqual(ids, ["final-webtest", "final-webtest-b2", "final-webtest-stop"])

    def test_unverified_batch_is_rechecked_with_its_report_and_no_fix_step(self):
        evs = [rep("final-webtest", "PASS"), rep("final-webtest-b2", "BLOCKED")]
        got = self.steps_of(self.tasks(WEBTEST_BATCH + 1), evs)
        self.assertEqual([s["id"] for s in got][-2:], ["final-webtest-b2-2", "final-webtest-stop"])
        self.assertNotIn("fix-plan-web", [s["id"] for s in got])
        again = next(s for s in got if s["id"] == "final-webtest-b2-2")
        self.assertIn("/r/final-webtest-b2-BLOCKED.md", again["prompt"])
        self.assertEqual(again["ok"], {"PASS"})

    def test_failed_batch_gets_fix_then_recheck(self):
        evs = [rep("final-webtest", "FAIL")]
        ids = [s["id"] for s in self.steps_of(self.tasks(2), evs)]
        self.assertEqual(ids, ["final-webtest", "fix-plan-web", "final-webtest-2", "final-webtest-stop"])


class FixTasksTest(BatchTest):
    def test_fix_tasks_get_their_own_batch_and_the_layout_stays(self):
        tasks = self.tasks(5)  # originals task-1..5: b1 = 1-4, b2 = [5]
        fix = self.tasks(7)[5:]  # fix tasks task-6, task-7 added by the fix round
        t0 = 100.0
        evs = [
            *({"ev": "dispatch", "task": f"task-{i}", "t": t0 - 10} for i in range(1, 6)),
            {"ev": "dispatch", "task": "fix-plan-web", "t": t0},
            {"ev": "report", "task": "fix-plan-web", "verdict": "DONE", "file": "/r/f.md", "t": t0 + 1},
            rep("final-webtest", "FAIL"),
            rep("final-webtest-b2", "PASS"),
            rep("final-webtest-2", "PASS"),
        ]
        ids = [s["id"] for s in self.steps_of(tasks + fix, evs)]
        self.assertNotIn("final-webtest-b3", ids)
        self.assertIn("final-webtest-f1", ids)
        step_f1 = next(s for s in self.steps_of(tasks + fix, evs) if s["id"] == "final-webtest-f1")
        self.assertIn("task-6, task-7", step_f1["prompt"])
        self.assertNotIn("task-5", step_f1["prompt"].split("tasks ")[1].split(" on branch")[0])


class VerdictTest(unittest.TestCase):
    def test_ux_clean_prose_and_none_are_not_unverified(self):
        for text in (
            "VERDICT: CLEAN\nPartial screenshots of the nav were fine",
            "VERDICT: CLEAN\nUNVERIFIED: none",
            "VERDICT: CLEAN\nUnverified: None - all checked",
        ):
            self.assertEqual(verdicts.verdict("ux-reviewer", text)[0], "CLEAN", text)

    def test_flows_path_must_stay_in_the_repo(self):
        self.assertTrue(ux_flows.path_problem("../x/f.json"))
        self.assertTrue(ux_flows.path_problem("/etc/passwd"))
        self.assertEqual(ux_flows.path_problem("workspace/n5/ux/f.json"), "")

    def test_ux_clean_with_unverified_is_fixes(self):
        self.assertEqual(verdicts.verdict("ux-reviewer", "VERDICT: CLEAN\nUNVERIFIED: finish screen: add flow")[0], "FIXES")
        self.assertEqual(verdicts.verdict("ux-reviewer", "VERDICT: CLEAN\nNOT verified: keyboard")[0], "CLEAN")
        self.assertEqual(verdicts.verdict("sec-auditor", "VERDICT: CLEAN\nUNVERIFIED: x")[0], "CLEAN")


GOOD = [{"name": "home", "covers": ["web/home.js"], "actions": []}, {"name": "finish", "covers": ["web/fin*.js"], "actions": [{"click": "#a"}, {"eval": "1"}]}]


class FlowsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

    def write(self, data, name="flows.json"):
        p = os.path.join(self.d, name)
        Path(p).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return p

    def test_reads_a_good_file(self):
        flows, probs = ux_flows.read(self.write(GOOD))
        self.assertEqual((len(flows), probs), (2, []))

    def test_bad_files_give_problems(self):
        for bad in (
            "{",
            [],
            [{"name": ""}],
            [{"name": "a", "actions": [{"zap": 1}]}],
            [{"name": "a", "actions": []}, {"name": "a", "actions": []}],
            [{"name": "a", "actions": [], "covers": "x"}],
        ):
            flows, probs = ux_flows.read(self.write(bad))
            self.assertEqual(flows, [], bad)
            self.assertTrue(probs, bad)
        self.assertTrue(ux_flows.read(os.path.join(self.d, "nope.json"))[1])

    def test_covering(self):
        flows, _ = ux_flows.read(self.write(GOOD))
        hit, loose = ux_flows.covering(flows, ["web/home.js", "web/finish.js", "web/other.js"])
        self.assertEqual((hit, loose), (["home", "finish"], ["web/other.js"]))


class NestedFlowsTest(unittest.TestCase):
    def test_placeholder_path_in_a_nested_repo(self):
        cfg = {"ux_flows": "workspace/<course>/ux/flows.json"}
        v = ux_flows.variables("UX-VARS: course=n5\n")
        self.assertEqual(ux_flows.flows_rel(cfg, v), "workspace/n5/ux/flows.json")
        self.assertEqual(ux_flows.flows_file("/r", cfg, v), "/r/workspace/n5/ux/flows.json")
        self.assertIn("<course>", ux_flows.flows_rel(cfg, {}))  # unfilled: stays visible
        self.assertEqual(ux_flows.flows_rel({}, v), "")

    def test_brief_note_names_the_resolved_path(self):
        from pipeline import briefs

        d = tracked_mkdtemp()
        os.makedirs(os.path.join(d, ".work", "s"))
        Path(d, ".work", "s", "TASKS.md").write_text("UX-VARS: course=fr-numbers\n", encoding="utf-8")
        a = SimpleNamespace(stage="s", dir=d)
        note = briefs.ux_flows_note(a, {"ux_flows": "workspace/<course>/ux/flows.json"})
        self.assertIn("workspace/fr-numbers/ux/flows.json", note)
        self.assertEqual(briefs.ux_flows_note(a, {}), "")


class PlanCheckTest(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)

    def run_check(self, cfg, produced, flows=None):
        Path(self.root, "pipeline.json").write_text(json.dumps(cfg), encoding="utf-8")
        if flows is not None:
            Path(self.root, "flows.json").write_text(json.dumps(flows), encoding="utf-8")
        from unittest import mock

        with mock.patch.object(plan_check, "_stage_produced", return_value=produced):
            return plan_check.ux_flows_findings(self.d, [], self.root)

    def test_nothing_without_the_key(self):
        self.assertEqual(self.run_check({}, ["web/a.js"]), [])

    def test_missing_file_is_a_problem_unless_the_plan_makes_it(self):
        self.assertEqual(len(self.run_check({"ux_flows": "flows.json"}, ["web/a.js"])), 1)
        self.assertEqual(self.run_check({"ux_flows": "flows.json"}, ["flows.json"]), [])

    def test_uncovered_ui_file_warns_and_never_fails(self):
        got = self.run_check({"ux_flows": "flows.json"}, ["web/other.js", "web/home.js", "docs/x.md"], GOOD)
        self.assertEqual(len(got), 1)
        self.assertIn("web/other.js", got[0])
        self.assertNotIn("web/home.js", got[0])
        self.assertEqual(plan_check.errors_of(got), [])

    def test_unfilled_placeholder_is_not_checked_and_a_filled_one_is(self):
        cfg = {"ux_flows": "workspace/<course>/flows.json"}
        self.assertEqual(self.run_check(cfg, ["web/a.js"]), [])
        Path(self.d, "TASKS.md").write_text("UX-VARS: course=n5\n", encoding="utf-8")
        os.makedirs(os.path.join(self.root, "workspace", "n5"))
        Path(self.root, "workspace", "n5", "flows.json").write_text(json.dumps(GOOD), encoding="utf-8")
        got = self.run_check(cfg, ["web/other.js"])
        self.assertEqual(len(got), 1)
        self.assertIn("web/other.js", got[0])

    def test_invalid_file_is_a_problem(self):
        got = self.run_check({"ux_flows": "flows.json"}, [], [{"name": "a", "actions": [{"zap": 1}]}])
        self.assertEqual(plan_check.errors_of(got), got)


if __name__ == "__main__":
    unittest.main()
