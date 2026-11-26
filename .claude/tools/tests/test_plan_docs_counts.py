"""plan check `docs-count:` error: a hand-written doc task that pins a machine count or a line number in its Goal or Done (synthetic task files)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_docs_counts
from pipeline.configuration import DOCS_COUNT_PREFIX
from tests.support.tmpdirs import tracked_mkdtemp


def tid(n):
    """Task id for ordinal n (built here so the test text carries no fixed id)."""
    return "task-" + str(n)


HAND_COUNT, CODE_COUNT, INDEX_COUNT, HAND_PLAIN, HAND_LINES = (tid(n) for n in (1, 2, 3, 4, 5))


class DocsCounts(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.write(HAND_COUNT, "docs/guide.md", "Goal: G", "Done: C1: the guide gives the count of rows")
        self.write(CODE_COUNT, "src/app.py", "Goal: G", "Done: C1: the code reports a count of rows")
        self.write(INDEX_COUNT, "docs/INDEX.md", "Goal: G", "Done: C1: the index gives the number of files")
        self.write(HAND_PLAIN, "docs/guide.md", "Goal: G", "Done: C1: the guide names the behaviour of the rule")
        self.write(HAND_LINES, "ORCHESTRATOR.md", "Goal: G", "Done: C1: the page cites path:line for the rule")

    def write(self, t, paths, goal, done):
        Path(self.d, t + ".md").write_text(f"# {t}: title\nWave: W1\nPredicted paths: {paths}\n{goal}\n{done}\nTest: t\n", encoding="utf-8")

    def check(self, t):
        return plan_docs_counts.check_docs_counts(self.d, t)

    def test_a_hand_written_doc_task_that_pins_a_count_is_an_error(self):
        got = self.check(HAND_COUNT)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(f"{DOCS_COUNT_PREFIX} {HAND_COUNT}"))
        self.assertIn("docs/INDEX.md", got[0])
        self.assertEqual(plan_check.errors_of(got), got)  # an error: not filtered out as a warning

    def test_a_line_number_in_a_practices_or_orchestrator_doc_is_an_error(self):
        self.assertEqual(len(self.check(HAND_LINES)), 1)

    def test_a_code_task_with_a_count_is_fine(self):
        self.assertEqual(self.check(CODE_COUNT), [])

    def test_a_task_for_the_generated_index_is_fine(self):
        self.assertEqual(self.check(INDEX_COUNT), [])

    def test_a_hand_written_doc_task_without_count_words_is_fine(self):
        self.assertEqual(self.check(HAND_PLAIN), [])

    def test_a_count_word_outside_goal_and_done_is_fine(self):
        self.write(HAND_PLAIN, "docs/guide.md", "Goal: G", "Done: C1: the guide names the rule")
        with open(os.path.join(self.d, HAND_PLAIN + ".md"), "a", encoding="utf-8") as f:
            f.write("Note: the count of rows is in the index\n")
        self.assertEqual(self.check(HAND_PLAIN), [])

    def test_the_rule_gives_one_error_per_task(self):
        self.write(HAND_COUNT, "docs/guide.md", "Goal: the number of files and line numbers", "Done: C1: count of rows, path:line")
        self.assertEqual(len(self.check(HAND_COUNT)), 1)
