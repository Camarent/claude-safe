"""plan_quotes.check_quotes: a Done line that quotes a message (12+ characters after warning/message/text/prints) needs that text in a tracked file, or the task text creating it; a quote in a Test line is never read."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_quotes

TASK = "task-1"
MSG = "plan check: the stage has no tests"  # a message that exists in the tracked file
MISS = "plan check: the phase ran out of rope"  # a message that no tracked file holds
PREFIX = "quote-missing:"


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


class QuotesTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="synthetic-stage-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.repo = tempfile.mkdtemp(prefix="synthetic-repo-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "checks.py"), "w", encoding="utf-8") as f:
            f.write(f'print("{MSG}")\n')
        git(self.repo, "add", "checks.py")

    def task(self, body):
        with open(os.path.join(self.d, TASK + ".md"), "w", encoding="utf-8") as f:
            f.write(body)

    def check(self):
        return plan_quotes.check_quotes(self.d, [{"id": TASK, "depends": []}], repo=self.repo)

    def test_quote_present_in_tree_passes(self):
        self.task(f"Goal: keep the check\nDone: C1: the run prints '{MSG}'\n")
        self.assertEqual(self.check(), [])

    def test_quote_missing_from_tree_is_an_error(self):
        self.task(f"Goal: keep the check\nDone: C1: the run prints '{MISS}'\n")
        probs = self.check()
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith(f"{PREFIX} {TASK}:"))
        self.assertIn(MISS, probs[0])

    def test_task_that_creates_the_quote_passes(self):
        self.task(f"Goal: add the message '{MISS}' to checks.py\nDone: C1: the run prints '{MISS}'\n")
        self.assertEqual(self.check(), [])

    def test_quote_in_test_line_is_ignored(self):
        self.task(f"Goal: keep the check\nTest: cs-qrun -- grep -q '{MISS}' checks.py\nDone: C1: npm test passes\n")
        self.assertEqual(self.check(), [])

    def test_short_quote_is_not_checked(self):
        self.task("Goal: keep the check\nDone: C1: the run prints 'ok'\n")
        self.assertEqual(self.check(), [])


if __name__ == "__main__":
    unittest.main()
