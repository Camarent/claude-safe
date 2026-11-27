"""plan_run.run exit status and gaps: EXIT_LIMIT with the GAPS: block as the last output when plan-review gaps stay open after the fix rounds, 0 when none, 2 when blocked; cs-plan task fills Affected tests from the importers of its predicted paths."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, cli_plan, plan_run
from pipeline.configuration import EXIT_LIMIT, GAPS_FILE, MODEL_SONNET, PLAN_FILE, PLAN_REVIEW_REPORT_FILE, WORK_DIR
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

GAP = "plan-review: gap task 3 has no test line"


class PlanGapsRunTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.pf = os.path.join(self.d, "p")
        with open(self.pf, "w") as f:
            f.write("goal")
        self.folder = os.path.join(self.d, WORK_DIR, "f")
        os.makedirs(self.folder)
        self.report = os.path.join(self.folder, PLAN_REVIEW_REPORT_FILE)
        with open(os.path.join(self.folder, PLAN_FILE), "w") as f:  # Plan.md: the phase review route
            f.write("# plan\n")
        self.printed = []

    def args(self):
        return argparse.Namespace(dir=self.d, feature="f", prompt_file=self.pf, dry_run=False, model=MODEL_SONNET)

    def fake_reviewer(self, report_text):
        def session(args, cwd, timeout=None):
            with open(self.report, "w") as f:
                f.write(report_text)
            return {"result": "", "error": False}

        return session

    def run_plan(self, report_text, architect="DONE"):
        def plan_session(a, c, model, prompt, tag):  # architect and fix sessions; the architect answers ARCHITECT
            return architect if tag == "plan" else "DONE"

        with (
            mock.patch.object(claude, "session", side_effect=self.fake_reviewer(report_text)),
            mock.patch.object(plan_run, "session", side_effect=plan_session),
            mock.patch.object(plan_run, "code_context", return_value=""),
            mock.patch.object(plan_run, "check_now", return_value=(3, [])),
            mock.patch.object(plan_run, "testrun_problems", return_value=[]),
            mock.patch.object(plan_run, "step"),
            mock.patch.object(plan_run, "blocked_report", return_value="blocked"),
            mock.patch("builtins.print", side_effect=lambda *x, **k: self.printed.append(" ".join(map(str, x)))),
        ):
            return plan_run.run(self.args())

    def gaps_file(self):
        return os.path.join(self.folder, GAPS_FILE)

    def test_gaps_after_fix_round_exit_limit_and_block_is_last_output(self):
        rc = self.run_plan("GAP: task 3 has no test line\nVERDICT: FAIL\n")
        self.assertEqual(rc, EXIT_LIMIT)
        self.assertEqual(self.printed[-1].splitlines(), ["GAPS:", GAP])
        self.assertIn("OK 3 tasks", "\n".join(self.printed[:-1]))
        self.assertEqual(Path(self.gaps_file()).read_text(encoding="utf-8"), f"GAPS:\n{GAP}\n")

    def test_done_exit_zero_and_no_gaps_block(self):
        rc = self.run_plan("VERDICT: DONE\n")
        self.assertEqual(rc, 0)
        self.assertFalse(any(line.startswith("GAPS:") for line in self.printed))
        self.assertFalse(os.path.exists(self.gaps_file()))

    def test_done_removes_gaps_file_of_earlier_run(self):
        Path(self.gaps_file()).write_text("GAPS:\nold\n", encoding="utf-8")
        self.assertEqual(self.run_plan("VERDICT: DONE\n"), 0)
        self.assertFalse(os.path.exists(self.gaps_file()))

    def test_blocked_exit_two_without_gaps_block(self):
        rc = self.run_plan("VERDICT: FAIL\nGAP: x\n", architect="BLOCKED")
        self.assertEqual(rc, 2)
        self.assertFalse(any(line.startswith("GAPS:") for line in self.printed))


class TaskAffectedTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)

    def repo(self):
        d = tracked_mkdtemp()
        subprocess.run(["git", "-C", d, "init", "-q"], check=True)  # plan check wants Predicted paths inside a git repo
        Path(d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False}), encoding="utf-8")
        for rel, text in (
            (".claude/tools/pipeline/mod.py", "X = 1\n"),
            (".claude/tools/tests/test_mod.py", "from pipeline import mod\n"),
            (".claude/tools/tests/test_other.py", "import os\n"),
        ):
            Path(d, rel).parent.mkdir(parents=True, exist_ok=True)
            Path(d, rel).write_text(text, encoding="utf-8")
        return d

    def p(self, d, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", d])
        return rc, out.getvalue(), err.getvalue()

    def task_text(self, d):
        return Path(d, ".work", "s", "task-1.md").read_text(encoding="utf-8")

    def task_argv(self, *extra):
        return ["task", "--stage", "s", "--id", "1", "--title", "Do it", "--paths", ".claude/tools/pipeline/mod.py", "--test", "t", "--done", "ok", *extra]

    def init(self, d):
        self.assertEqual(
            self.p(d, "stage-init", "--stage", "s", "--goal", "g", "--flags", "no-merge no-test no-audit no-retro no-index no-push no-clean")[0], 0
        )

    def test_affected_filled_from_importers(self):
        d = self.repo()
        self.init(d)
        rc, _, err = self.p(d, *self.task_argv())
        self.assertEqual(rc, 0, err)
        self.assertIn("Affected tests: test_mod.py\n", self.task_text(d))

    def test_given_affected_is_kept(self):
        d = self.repo()
        self.init(d)
        self.assertEqual(self.p(d, *self.task_argv("--affected", "test_other.py"))[0], 0)
        self.assertIn("Affected tests: test_other.py\n", self.task_text(d))
        self.assertNotIn("test_mod.py", self.task_text(d))

    def test_no_importer_writes_no_affected_line(self):
        d = self.repo()
        self.init(d)
        argv = self.task_argv()
        argv[argv.index("--paths") + 1] = "b/y.js"
        self.assertEqual(self.p(d, *argv)[0], 0)
        self.assertNotIn("Affected tests:", self.task_text(d))


if __name__ == "__main__":
    unittest.main()
