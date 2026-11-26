"""`research` task type: TASK_TYPES membership, `Kind:` header read by task_type, `Outputs:` helper."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_files


class ResearchKindTest(unittest.TestCase):
    def test_research_is_a_task_type(self):
        self.assertIn("research", plan_files.TASK_TYPES)

    def test_type_header_research(self):
        self.assertEqual(plan_files.task_type("# t\nType: research\nUI: no\n"), "research")

    def test_kind_header_research(self):
        self.assertEqual(plan_files.task_type("# t\nKind: research\nUI: no\n"), "research")

    def test_kind_header_is_case_insensitive(self):
        self.assertEqual(plan_files.task_type("# t\nKind: Research\n"), "research")

    def test_no_header_is_code(self):
        self.assertEqual(plan_files.task_type("# t\nUI: no\n"), "code")

    def test_outputs_none_one_and_two(self):
        self.assertEqual(plan_files.task_outputs("# t\nUI: no\n"), [])
        self.assertEqual(plan_files.task_outputs("Outputs: `notes/a.md`\n"), ["notes/a.md"])
        self.assertEqual(plan_files.task_outputs("Outputs: a.md, `b/c.csv`\n"), ["a.md", "b/c.csv"])


if __name__ == "__main__":
    unittest.main()
