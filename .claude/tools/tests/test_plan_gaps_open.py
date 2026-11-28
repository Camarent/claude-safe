"""plan_gaps_open.open_gaps reads the GAPS and plan-review gap files of a folder; cli_wave._warn_open_gaps prints them as a warning block and prints nothing for a stage without gap files."""

import io
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_wave, plan_gaps_open
from pipeline.configuration import GAPS_FILE, PLAN_REVIEW_GAPS_FILE
from tests.support.tmpdirs import tracked_mkdtemp

GAP = "plan-review: gap task 3 has no test line"


class OpenGapsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def write(self, name, text):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_no_gap_files_gives_empty(self):
        self.assertEqual(plan_gaps_open.open_gaps(self.d), [])

    def test_gaps_md_lines_read(self):
        self.write(GAPS_FILE, f"GAPS:\n{GAP}\n\n")
        self.assertEqual(plan_gaps_open.open_gaps(self.d), ["GAPS:", GAP])

    def test_plan_review_gaps_file_read(self):
        self.write(PLAN_REVIEW_GAPS_FILE, f"{GAP}\n")
        self.assertEqual(plan_gaps_open.open_gaps(self.d), [GAP])

    def test_warn_prints_open_gaps_and_returns(self):
        self.write(GAPS_FILE, f"{GAP}\n")
        out = io.StringIO()
        with redirect_stdout(out):
            cli_wave._warn_open_gaps(SimpleNamespace(w=self.d))
        self.assertIn("1 plan-review gap line(s) still open", out.getvalue())
        self.assertIn(f"  {GAP}", out.getvalue())

    def test_stage_without_gap_files_prints_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli_wave._warn_open_gaps(SimpleNamespace(w=self.d))
        self.assertEqual(out.getvalue(), "")

    def test_non_utf8_gap_file_still_read(self):
        with open(os.path.join(self.d, GAPS_FILE), "wb") as f:
            f.write(b"bad byte \xff here\n" + GAP.encode() + b"\n")
        lines = plan_gaps_open.open_gaps(self.d)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith(GAP))

    def test_run_locked_warns_then_runs_loop(self):
        self.write(GAPS_FILE, f"{GAP}\n")
        out = io.StringIO()
        with (
            mock.patch.object(cli_wave, "_run_guards", return_value=None),
            mock.patch.object(cli_wave, "_run_preflights", return_value=None),
            mock.patch.object(cli_wave.ops_preflight, "later_ops", return_value=[]),
            mock.patch.object(cli_wave, "run_loop", return_value=0) as loop,
            redirect_stdout(out),
        ):
            code = cli_wave._run_locked(SimpleNamespace(), SimpleNamespace(w=self.d))
        self.assertEqual(code, 0)
        loop.assert_called_once()
        self.assertIn("1 plan-review gap line(s) still open", out.getvalue())
        self.assertIn(f"  {GAP}", out.getvalue())


if __name__ == "__main__":
    unittest.main()
