"""retry --note lint: a note must name a path[:line]; a path outside the task's Predicted paths, or one its Done/Test lines forbid, is a warning."""

import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, retry_note

TASK = """# task-9: demo
Predicted paths: .claude/tools/pipeline/cli_orch.py, .claude/tools/tests/test_retry_note_lint.py
Done: C1: edit .claude/tools/pipeline/cli_orch.py only; do not edit scripts/release.sh
Test: cs-qrun -- scripts/dev.sh test test_retry_note_lint.py
"""
FAILING = "make it pass"
PASSING = "edit .claude/tools/pipeline/cli_orch.py:523 replace X with Y"


class RetryNoteLintTest(unittest.TestCase):
    def test_failing_example_is_refused(self):
        self.assertTrue(retry_note.refused(FAILING))
        msgs = retry_note.lint(FAILING, TASK)
        self.assertEqual(len(msgs), 1)
        self.assertIn("path[:line]", msgs[0])

    def test_number_like_tokens_are_not_paths(self):
        for note in ("make it 2.0x faster", "use e.g. a loop", "bump to v1.2"):
            self.assertTrue(retry_note.refused(note), note)

    def test_extensionless_path_with_line_is_a_path(self):
        for note in ("edit Makefile:12 add the target", "edit scripts/dev:3 replace X"):
            self.assertFalse(retry_note.refused(note), note)
        msgs = retry_note.lint("edit scripts/dev:3 replace X", TASK)
        self.assertEqual(len(msgs), 1)
        self.assertIn("outside the task's Predicted paths", msgs[0])

    def test_time_like_token_is_not_a_path(self):
        self.assertTrue(retry_note.refused("retry at 10:30 with more time"))

    def test_passing_example_is_accepted_without_warning(self):
        self.assertFalse(retry_note.refused(PASSING))
        self.assertEqual(retry_note.lint(PASSING, TASK), [])

    def test_path_outside_predicted_paths_warns(self):
        msgs = retry_note.lint("edit .claude/tools/pipeline/driver.py:10 replace X", TASK)
        self.assertEqual(len(msgs), 1)
        self.assertIn("outside the task's Predicted paths", msgs[0])

    def test_path_forbidden_by_done_line_warns(self):
        msgs = retry_note.lint("edit scripts/release.sh:3 replace X", TASK)
        self.assertEqual(len(msgs), 1)
        self.assertIn("forbid", msgs[0])

    def test_no_other_file_clause_does_not_forbid_the_named_file(self):
        task = TASK.replace("cli_orch.py only;", "cli_orch.py, no other file;")
        self.assertEqual(retry_note.lint(PASSING, task), [])

    def test_tail_path_matches_predicted_entry(self):
        self.assertEqual(retry_note.lint("edit pipeline/cli_orch.py:523 replace X", TASK), [])

    def test_cli_refuses_note_without_path(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_task(d)
            a = argparse.Namespace(cmd="retry", stage="s", dir=d, step="task-9", note=FAILING)
            with self.assertRaises(SystemExit) as cm:
                cli_orch._lint_retry_note(a)
            self.assertIn("path[:line]", str(cm.exception.code))

    def test_cli_prints_warning_and_goes_on(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_task(d)
            a = argparse.Namespace(cmd="retry", stage="s", dir=d, step="task-9", note="edit scripts/release.sh:3 replace X")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cli_orch._lint_retry_note(a)
            self.assertIn("warning", err.getvalue())

    def _write_task(self, d):
        folder = os.path.join(d, cli_orch.WORK_DIR, "s")
        os.makedirs(folder)
        with open(os.path.join(folder, "task-9.md"), "w", encoding="utf-8") as f:
            f.write(TASK)


if __name__ == "__main__":
    unittest.main()
