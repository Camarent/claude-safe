"""Deny reasons of write_guard, review_guard and model_guard: line 1 names the allowed form (and the worktree path for write_guard)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import model_guard
import review_guard
import write_guard


def first_line(text):
    return text.split("\n", 1)[0]


class WriteGuardReasonTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.main = os.path.realpath(os.path.join(d.name, "main"))
        self.worktree = os.path.realpath(os.path.join(d.name, "wt"))

    def test_line_one_names_the_retry_path_under_the_worktree(self):
        path = os.path.join(self.main, "docs", "a.md")
        reason = write_guard.deny_reason(path, self.main, self.worktree)
        retry = os.path.join(self.worktree, "docs", "a.md")
        self.assertTrue(first_line(reason).startswith(f"Write {retry} "), reason)
        self.assertIn(self.worktree, first_line(reason))

    def test_scratch_hint_keeps_the_form_on_line_one(self):
        path = os.path.join(self.main, "tmp", "x.txt")
        reason = write_guard.deny_reason(path, self.main, self.worktree)
        self.assertTrue(first_line(reason).startswith("Write "), reason)
        self.assertIn("Scratch files go to the", reason)


class ReviewGuardReasonTest(unittest.TestCase):
    def test_line_one_names_the_single_call_shape(self):
        reason = review_guard.segment_verdict('echo "unclosed', os.sep, os.sep)[0]
        self.assertTrue(first_line(reason).startswith("Run one plain command per Bash call"), reason)
        self.assertIn("unparsable command", reason)


class ModelGuardReasonTest(unittest.TestCase):
    def test_line_one_names_the_model_to_use(self):
        reason = model_guard.verdict({"subagent_type": "dev", "model": "sonnet", "prompt": "count lines"})
        self.assertTrue(first_line(reason).startswith("Use model haiku"), reason)
        self.assertIn("MODEL-REASON:", reason)


if __name__ == "__main__":
    unittest.main()
