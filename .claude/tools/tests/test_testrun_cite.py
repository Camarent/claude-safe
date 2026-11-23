"""testrun runs the cite check after the Test lines are green: a wrong path:line cite in the task's added lines or the dev report is FAIL; `cite_check` false in pipeline.json switches it off."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testrun
from pipeline.configuration import CITE_CHECK_KEY, JOB_DIFF_MAX_CHARS
from tests.support.tmpdirs import tracked_mkdtemp

GOOD_CITE = "see pipeline/code.py:2"  # real line 2 of the source below
BAD_CITE = "see pipeline/code.py:99"  # past the end of the source
SOURCE = "a = 1\nb = 2\n"


def diff_of(prose):
    """A committed diff (`git diff -U0`-like) that adds one doc line."""
    return f"diff --git a/docs/note.md b/docs/note.md\n--- a/docs/note.md\n+++ b/docs/note.md\n@@ -0,0 +1 @@\n+{prose}\n"


def box(test_lines, cite_switch=None, report=""):
    d = tracked_mkdtemp()
    os.makedirs(f"{d}/.work/s")
    os.makedirs(f"{d}/pipeline")
    os.makedirs(f"{d}/docs")
    with open(f"{d}/pipeline/code.py", "w") as f:
        f.write(SOURCE)
    with open(f"{d}/.work/s/task-1.md", "w") as f:
        f.write("# t\n" + test_lines)
    if cite_switch is not None:
        with open(f"{d}/pipeline.json", "w") as f:
            json.dump({CITE_CHECK_KEY: cite_switch}, f)
    if report:
        with open(f"{d}/.work/s/report-x.md", "w") as f:
            f.write(report)
    return d


def green(argv, **_kw):
    return subprocess.CompletedProcess(argv, 0, "", "")


def red(argv, **_kw):
    return subprocess.CompletedProcess(argv, 1, "boom", "")


def scan_clean(*_a):
    return []


class TestRunCite(unittest.TestCase):
    def run_task(self, d, prose, runner=green, report=""):
        return testrun.run("s", "task-1", d, runner=runner, scan=scan_clean, diff_fn=lambda *_a: diff_of(prose), report=report)

    def test_wrong_cite_in_added_line_is_fail_with_message(self):
        d = box("Test: true\n")
        verdict, note = self.run_task(d, BAD_CITE)
        self.assertEqual(verdict, testrun.FAIL)
        self.assertIn("FAIL cite check", note)
        self.assertIn("docs/note.md:1 cites pipeline/code.py:99", note)

    def test_correct_cite_is_done(self):
        d = box("Test: true\n")
        verdict, note = self.run_task(d, GOOD_CITE)
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("cite check", note)

    def test_switch_false_is_done_despite_wrong_cite(self):
        d = box("Test: true\n", cite_switch=False)
        verdict, note = self.run_task(d, BAD_CITE)
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("cite check", note)

    def test_wrong_cite_in_dev_report_is_fail(self):
        d = box("Test: true\n", report=f"done, {BAD_CITE}\n")
        verdict, note = self.run_task(d, GOOD_CITE, report=os.path.join(d, ".work", "s", "report-x.md"))
        self.assertEqual(verdict, testrun.FAIL)
        self.assertIn("report:1 cites pipeline/code.py:99", note)

    def test_unavailable_job_diff_is_done_with_skip_note(self):
        d = box("Test: true\n")
        verdict, note = testrun.run("s", "task-1", d, runner=green, scan=scan_clean, diff_fn=lambda *_a: None)
        self.assertEqual(verdict, testrun.DONE)
        self.assertIn("cite check skipped: the job diff is unavailable", note)

    def test_cite_past_the_old_diff_cut_is_checked(self):
        d = box("Test: true\n")
        long_prose = "x" * (JOB_DIFF_MAX_CHARS + 1000)  # a long added line pushes the bad cite past the old 14000-char cut
        full = f"diff --git a/docs/note.md b/docs/note.md\n--- a/docs/note.md\n+++ b/docs/note.md\n@@ -0,0 +1,2 @@\n+{long_prose}\n+{BAD_CITE}\n"

        def cut_diff(_stage, _task, _d, limit):
            return full[:limit]

        verdict, note = testrun.run("s", "task-1", d, runner=green, scan=scan_clean, diff_fn=cut_diff)
        self.assertEqual(verdict, testrun.FAIL)
        self.assertIn("cites pipeline/code.py:99", note)

    def test_bad_cite_in_code_string_literal_is_done(self):
        d = box("Test: true\n")
        diff = 'diff --git a/pipeline/x.py b/pipeline/x.py\n--- a/pipeline/x.py\n+++ b/pipeline/x.py\n@@ -0,0 +1 @@\n+X = "pipeline/code.py:99"\n'
        verdict, note = testrun.run("s", "task-1", d, runner=green, scan=scan_clean, diff_fn=lambda *_a: diff)
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("cite check", note)

    def test_red_test_line_keeps_old_behaviour_no_cite_check(self):
        d = box("Test: true\n")
        verdict, note = self.run_task(d, BAD_CITE, runner=red)
        self.assertEqual(verdict, testrun.FAIL)
        self.assertIn("exited 1", note)
        self.assertNotIn("cite check", note)

    def test_cli_report_option_reaches_run(self):
        d = box("Test: true\n", report="x\n")
        report = os.path.join(d, ".work", "s", "report-x.md")
        seen = {}

        def fake_run(stage, task, _dd, report="", **_kw):
            seen["report"] = report
            return testrun.DONE, "ok"

        with mock.patch.object(testrun, "run", fake_run):
            rc = testrun.main(["--stage", "s", "--task", "task-1", "--dir", d, "--report", report])
        self.assertEqual(rc, 0)
        self.assertEqual(seen["report"], report)
        self.assertTrue(Path(report).is_file())


if __name__ == "__main__":
    unittest.main()
