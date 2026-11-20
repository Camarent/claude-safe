"""plan_rules.check_docs_target: the `docs` flag needs a docs target (synthetic files)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_rules


class DocsTarget(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = td.name
        self.tasks = os.path.join(self.d, "TASKS.md")

    def write(self, name, text):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
            f.write(text)

    def check(self):
        return plan_rules.check_docs_target(self.tasks, self.d)

    def test_no_flag_means_no_target_needed(self):
        self.write("TASKS.md", "flags: ui\n")
        self.assertEqual(self.check(), [])

    def test_flag_without_target_is_a_problem(self):
        self.write("TASKS.md", "flags: ui docs\n")
        self.assertIn("no docs target", self.check()[0])

    def test_each_target_source_satisfies_it(self):
        self.write("TASKS.md", "flags: docs\ndocs: skip\n")
        self.assertEqual(self.check(), [])
        self.write("TASKS.md", "flags: docs\n")
        self.write("PROGRESS.md", "docs: docs/\n")
        self.assertEqual(self.check(), [])
        os.remove(os.path.join(self.d, "PROGRESS.md"))
        self.write("CLAUDE.md", "# x\n## Docs\ndocs/\n")
        self.assertEqual(self.check(), [])

    def test_it_is_an_error_not_a_warning(self):
        from pipeline import plan_check

        self.write("TASKS.md", "flags: docs\n")
        self.assertEqual(plan_check.errors_of(self.check()), self.check())


if __name__ == "__main__":
    unittest.main()
