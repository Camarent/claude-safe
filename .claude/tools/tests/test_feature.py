"""Synthetic tests for `wave.py feature` (phase driver): order, planning, stops, done_when. Stage runs and the architect are stubbed."""

import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_feature, cli_plan, cli_wave

os.environ["PIPELINE_NO_LEADS"] = "1"  # no first code search (local.py find) in the driver runs
from pipeline import plan_files as pf
from pipeline.run import Run
from tests.isolated import isolate_cwd
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp


class FeatureDriver(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q", "-b", "master")  # own repo: a branch switch by the driver stays in this temp repo, never in the checkout above TMPDIR
        git(self.d, "commit", "-q", "--allow-empty", "-m", "init")
        cli_plan.main(["plan-init", "--feature", "F", "--goal", "g", "--dir", self.d])
        prev = None
        for n, done in (("a", "true"), ("b", "true"), ("c", "true")):
            cli_plan.main(
                [
                    "phase",
                    "--feature",
                    "F",
                    "--name",
                    n,
                    "--goal",
                    n,
                    "--done-when",
                    done,
                    "--paths",
                    f"src/{n}",
                    *(["--depends", prev] if prev else []),
                    "--dir",
                    self.d,
                ]
            )
            prev = n
        self.ran = []

    def plan(self, *names):
        for n in names:
            f = os.path.join(self.d, ".work", "F", "phases", n)
            os.makedirs(f, exist_ok=True)
            with open(os.path.join(f, "TASKS.md"), "w") as t:
                t.write("# Stage\n")

    def feature_args(self):
        return cli_wave.argparse.Namespace(feature="F", dir=self.d, max_waves=12, max_dispatches=60, max_budget_usd=None, dry_run=False)

    def feature_lock(self):
        return os.path.join(self.d, ".work", "F", "feature.lock")

    def test_live_feature_lock_refuses_a_second_driver(self):
        self.assertTrue(cli_wave._take_lock(self.feature_lock()))  # another driver holds it
        self.addCleanup(cli_wave._release_lock, self.feature_lock())
        with mock.patch.object(cli_feature, "dirty_block", side_effect=AssertionError("guards ran")), redirect_stdout(StringIO()) as out:
            rc = cli_feature.cmd_feature(self.feature_args())
        self.assertEqual(rc, 4)
        self.assertIn("already runs", out.getvalue())
        self.assertTrue(os.path.exists(self.feature_lock()))  # the other driver keeps its lock

    def test_feature_lock_is_released_after_a_block(self):
        with mock.patch.object(cli_feature, "dirty_block", return_value="dirty tree"), redirect_stdout(StringIO()):
            rc = cli_feature.cmd_feature(self.feature_args())
        self.assertEqual(rc, 4)
        self.assertFalse(os.path.exists(self.feature_lock()))

    def run_feature(self, run_rc=lambda s: 0, plan_problems=None, dry=False):
        def fake_run(ns):
            self.ran.append(ns.stage)
            return run_rc(ns.stage)

        def fake_plan(a, phase):
            self.plan(phase["dir"])
            return plan_problems or []

        a = cli_wave.argparse.Namespace(feature="F", dir=self.d, max_waves=12, max_dispatches=60, max_budget_usd=None, dry_run=dry)
        out = StringIO()
        with mock.patch.object(cli_wave, "cmd_run", fake_run), mock.patch.object(cli_feature, "plan_phase", fake_plan), redirect_stdout(out):
            rc = cli_feature.cmd_feature(a)
        return rc, out.getvalue()

    def test_plan_phase_logs_phase_size(self):
        import json

        pf.write_phases(
            cli_wave.argparse.Namespace(dir=self.d, feature="F"),
            [{**p, "tasks": 12} for p in pf.read_phases(cli_wave.argparse.Namespace(dir=self.d, feature="F"))],
        )
        a = cli_wave.argparse.Namespace(feature="F", dir=self.d)
        phase = pf.read_phases(a)[0]
        self.plan("01-a")
        with mock.patch.object(cli_feature.claude, "session"), mock.patch.object(cli_feature.plan_check, "check_stage", return_value=(7, [])):
            self.assertEqual(cli_feature.plan_phase(a, phase), [])
        with open(os.path.join(self.d, ".work", "F", "metrics.jsonl"), encoding="utf-8") as f:
            ev = [json.loads(line) for line in f][-1]
        self.assertEqual((ev["ev"], ev["estimate"], ev["actual"]), ("phase-size", 12, 7))

    def test_plan_phase_prints_status_lines(self):
        a = cli_wave.argparse.Namespace(feature="F", dir=self.d)
        phase = pf.read_phases(a)[0]
        self.plan("01-a")
        with (
            mock.patch.object(cli_feature.claude, "session"),
            mock.patch.object(cli_feature.plan_check, "check_stage", return_value=(7, [])),
            redirect_stdout(StringIO()) as out,
        ):
            cli_feature.plan_phase(a, phase)
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("[p1/") and "01-a] planning started" in lines[0], lines)
        self.assertIn("architect planning", lines[1])
        self.assertTrue(lines[2].endswith("planned: 7 tasks"), lines)

    def test_plan_phase_prints_warnings_and_returns_only_errors(self):
        a = cli_wave.argparse.Namespace(feature="F", dir=self.d)
        phase = pf.read_phases(a)[0]
        self.plan("01-a")
        found = [
            "shared: x.jsonl is shared",
            "size: task-1: 9 predicted files",
            "shared-file: ids.js is listed by task-1, task-2, task-3",
            "task-2: bad",
        ]
        with (
            mock.patch.object(cli_feature.claude, "session"),
            mock.patch.object(cli_feature.plan_check, "check_stage", return_value=(3, found)),
            redirect_stdout(StringIO()) as out,
        ):
            probs = cli_feature.plan_phase(a, phase)
        self.assertEqual(probs, [found[2], found[3]])
        printed = out.getvalue().splitlines()
        self.assertIn(found[0], printed)
        self.assertIn(found[1], printed)
        self.assertNotIn(found[2], printed)

    def status(self):
        return [p["status"] for p in pf.read_phases(cli_wave.argparse.Namespace(dir=self.d, feature="F"))]

    def test_runs_every_phase_in_order_plans_missing_ones_and_marks_done(self):
        self.plan("01-a")  # 02-b and 03-c get planned by the (stubbed) architect when their turn comes
        rc, out = self.run_feature()
        self.assertEqual(rc, 0)
        self.assertEqual(self.ran, ["F/phases/01-a", "F/phases/02-b", "F/phases/03-c", "F/phases/04-finish"])  # the closing phase is added by the driver
        self.assertEqual(self.status(), ["done", "done", "done", "done"])
        self.assertIn("all 4 phases done", out)
        self.assertTrue(os.path.exists(os.path.join(self.d, ".work", "F", "phases", "04-finish", "TASKS.md")))  # a stub, no architect session

    def test_dry_run_shows_the_closing_phase_and_changes_nothing(self):
        self.plan("01-a")
        rc, out = self.run_feature(dry=True)
        self.assertEqual(rc, 0)
        self.assertIn("04-finish: the driver adds it", out)
        self.assertEqual(self.status(), ["todo", "todo", "todo"])

    def test_dry_run_shows_the_closing_phase_when_all_phases_are_done(self):
        for n in ("01-a", "02-b", "03-c"):
            cli_feature.mark_phase_done(Run(dir=self.d, feature="F"), n)
        rc, out = self.run_feature(dry=True)
        self.assertEqual(rc, 0)
        self.assertIn("04-finish: the driver adds it", out)

    def test_status_lists_the_closing_phase_before_the_driver_adds_it(self):
        self.plan("01-a")
        out = StringIO()
        with redirect_stdout(out):
            cli_wave.cmd_status(cli_wave.argparse.Namespace(dir=self.d, stage="F"))
        self.assertIn("p4 04-finish: closing phase, no tasks of its own", out.getvalue())
        self.assertIn("p4/4 04-finish: todo, closing phase", out.getvalue())
        self.assertEqual(self.status(), ["todo", "todo", "todo"])  # status adds nothing to Plan.md

    def test_a_stage_that_asks_the_lead_stops_the_feature_and_resumes_there(self):
        self.plan("01-a", "02-b", "03-c")
        rc, _ = self.run_feature(run_rc=lambda s: 4 if s.endswith("02-b") else 0)
        self.assertEqual(rc, 4)
        self.assertEqual(self.status(), ["done", "todo", "todo", "todo"])
        self.ran.clear()
        rc, _ = self.run_feature()
        self.assertEqual((rc, self.ran), (0, ["F/phases/02-b", "F/phases/03-c", "F/phases/04-finish"]))  # the finished phase is not run again

    def test_failed_done_when_blocks_and_the_phase_stays_open(self):
        self.plan("01-a")
        phases = pf.read_phases(cli_wave.argparse.Namespace(dir=self.d, feature="F"))
        phases[0]["done_when"] = "exit 3"
        pf.write_phases(cli_wave.argparse.Namespace(dir=self.d, feature="F"), phases)
        rc, out = self.run_feature()
        self.assertEqual(rc, 4)
        self.assertEqual(self.status()[0], "todo")
        self.assertIn("done_when failed", out)
        self.assertTrue(os.path.exists(os.path.join(self.d, ".work", "F", "BLOCKED.md")))

    def test_manual_done_when_pauses_the_lead_and_is_not_run_as_a_shell_command(self):
        self.plan("01-a")
        phases = pf.read_phases(cli_wave.argparse.Namespace(dir=self.d, feature="F"))
        phases[0]["done_when"] = "MANUAL: open the dashboard"
        pf.write_phases(cli_wave.argparse.Namespace(dir=self.d, feature="F"), phases)
        rc, out = self.run_feature()
        self.assertEqual(rc, 4)
        self.assertEqual(self.status()[0], "todo")
        self.assertIn("manual check for the lead", out)
        self.assertIn("open the dashboard", out)
        self.assertNotIn("done_when failed", out)

    def test_planning_problems_block_without_running_the_stage(self):
        rc, out = self.run_feature(plan_problems=["task-1: bad"])
        self.assertEqual(rc, 4)
        self.assertEqual(self.ran, [])
        self.assertIn("could not be planned", out)

    def test_dry_run_changes_nothing(self):
        self.plan("01-a")
        rc, out = self.run_feature(dry=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.status(), ["todo", "todo", "todo"])
        self.assertEqual(self.ran, [])
        self.assertIn("NOT PLANNED", out)
        self.assertIn("phase 03-c", out)

    def test_single_stage_plan_runs_as_one_stage(self):
        d = tracked_mkdtemp()
        git(d, "init", "-q", "-b", "master")  # own repo, as in setUp
        git(d, "commit", "-q", "--allow-empty", "-m", "init")
        f = os.path.join(d, ".work", "S")
        os.makedirs(f)
        with open(os.path.join(f, "TASKS.md"), "w") as t:
            t.write("# Stage\n")
        ran = []
        a = cli_wave.argparse.Namespace(feature="S", dir=d, max_waves=12, max_dispatches=60, max_budget_usd=None, dry_run=False)
        with mock.patch.object(cli_wave, "cmd_run", lambda ns: ran.append(ns.stage) or 0), redirect_stdout(StringIO()):
            self.assertEqual(cli_feature.cmd_feature(a), 0)
        self.assertEqual(ran, ["S"])

    def test_no_phases_and_no_tasks_still_blocks(self):
        d = tracked_mkdtemp()
        git(d, "init", "-q", "-b", "master")  # own repo, as in setUp
        git(d, "commit", "-q", "--allow-empty", "-m", "init")
        os.makedirs(os.path.join(d, ".work", "S"))
        a = cli_wave.argparse.Namespace(feature="S", dir=d, max_waves=12, max_dispatches=60, max_budget_usd=None, dry_run=False)
        with redirect_stdout(StringIO()):
            self.assertEqual(cli_feature.cmd_feature(a), 4)


if __name__ == "__main__":
    unittest.main()
