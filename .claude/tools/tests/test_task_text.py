"""task_text: a field counts only at a line start and outside fenced code blocks; the anchor is what keeps decoys out."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import task_text

TASK = """# task-9: Example
Goal: fix the thing
Test: cs-qrun -- pytest x.py
Done: C1: the thing works; proven by test_x.py
"""

# a Done line whose value names a later-notes file, as a planned task carries it
M25_TASK = """# task-4: Notes
Goal: record the notes
Done: C1: notes written to .work/retro-m25/phases/01-helpers-plan-checks/later-notes.md; the file lists every decision
Test: cs-qrun -- scripts/dev.sh test test_task_text.py
"""


class FieldTest(unittest.TestCase):
    def test_field_gives_each_value_stripped(self):
        self.assertEqual(task_text.field(TASK, "Done"), ["C1: the thing works; proven by test_x.py"])
        self.assertEqual(task_text.field(TASK, "Goal"), ["fix the thing"])

    def test_missing_field_gives_empty_list(self):
        self.assertEqual(task_text.field(TASK, "Outputs"), [])

    def test_empty_field_gives_empty_string(self):
        self.assertEqual(task_text.field("Done:   \n", "Done"), [""])

    def test_mid_line_decoy_is_not_a_field(self):
        text = "Goal: see x Done: y in the notes\nprose Done: z\n"
        self.assertEqual(task_text.field(text, "Done"), [])

    def test_mutation_anchor_dropped_would_match_mid_line(self):
        # the anchor is what keeps a mid-line `Done:` out: without it this text yields "y"
        text = "x Done: y\n"
        self.assertEqual(task_text.field(text, "Done"), [])
        self.assertNotIn("y", task_text.field(text, "Done"))

    def test_fenced_block_decoy_is_not_a_field(self):
        text = "Goal: g\n```\nDone: fake\n```\nDone: real\n"
        self.assertEqual(task_text.field(text, "Done"), ["real"])

    def test_tilde_fence_and_longer_closing_fence(self):
        text = "~~~text\nDone: fake\n~~~~\nDone: after\n"
        self.assertEqual(task_text.field(text, "Done"), ["after"])

    def test_longer_fence_is_not_closed_by_a_shorter_run(self):
        text = "````\n```\nDone: fake\n````\nDone: real\n"
        self.assertEqual(task_text.field(text, "Done"), ["real"])

    def test_fence_closes_only_on_same_character(self):
        text = "```\nDone: fake\n~~~\nDone: still-fake\n```\nDone: real\n"
        self.assertEqual(task_text.field(text, "Done"), ["real"])

    def test_m25_done_line_parses_to_one_value(self):
        values = task_text.field(M25_TASK, "Done")
        self.assertEqual(len(values), 1)
        self.assertIn("later-notes.md", values[0])


class LinesTest(unittest.TestCase):
    def test_lines_gives_name_value_pairs_in_file_order(self):
        self.assertEqual(
            task_text.lines(TASK, "Done", "Goal", "Test"),
            [("Goal", "fix the thing"), ("Test", "cs-qrun -- pytest x.py"), ("Done", "C1: the thing works; proven by test_x.py")],
        )

    def test_lines_skips_names_not_asked_for(self):
        self.assertEqual(task_text.lines(TASK, "Goal"), [("Goal", "fix the thing")])

    def test_lines_without_names_is_empty(self):
        self.assertEqual(task_text.lines(TASK), [])

    def test_lines_name_is_matched_as_a_whole_word_before_the_colon(self):
        text = "Doneish: no\nDone: yes\n"
        self.assertEqual(task_text.lines(text, "Done"), [("Done", "yes")])

    def test_lines_respects_fences(self):
        text = "```\nTest: fake\n```\nTest: real\n"
        self.assertEqual(task_text.lines(text, "Test"), [("Test", "real")])

    def test_lines_m25_done_line(self):
        self.assertEqual(
            [name for name, _ in task_text.lines(M25_TASK, "Goal", "Done", "Test")],
            ["Goal", "Done", "Test"],
        )


if __name__ == "__main__":
    unittest.main()
