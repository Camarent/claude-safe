import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.util import work_path, work_subdir


class WorkPathTest(unittest.TestCase):
    def test_subdirs(self):
        for name, sub in [
            ("report-a1.md", "reports"),
            ("leads-plan.md", "leads"),
            ("leads-plan-rel.md", "leads"),
            ("audit-context.md", "audits"),
            ("audit-sec-2.md", "audits"),
            ("audit-ux.md", "audits"),
            ("scans.md", "audits"),
            ("tools-check.md", "audits"),
            ("plan-vs-changes.md", "audits"),
            ("retro.md", "retro"),
            ("retro-digest.md", "retro"),
            ("retro-numbers.md", "retro"),
            ("incidents.json", "retro"),
            ("TASKS.md", ""),
            ("orch.jsonl", ""),
            ("metrics.jsonl", ""),
            ("BLOCKED.md", ""),
            ("task-1.md", ""),
        ]:
            self.assertEqual(work_subdir(name), sub, name)

    def test_create_and_new_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = work_path(d, "report-a1.md", create=True)
            self.assertEqual(p, os.path.join(d, "reports", "report-a1.md"))
            self.assertTrue(os.path.isdir(os.path.join(d, "reports")))
            self.assertEqual(work_path(d, "TASKS.md", create=True), os.path.join(d, "TASKS.md"))

    def test_reader_falls_back_to_old_top_level(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(work_path(d, "retro.md"), os.path.join(d, "retro", "retro.md"))
            open(os.path.join(d, "retro.md"), "w").close()
            self.assertEqual(work_path(d, "retro.md"), os.path.join(d, "retro.md"))
            os.makedirs(os.path.join(d, "retro"))
            open(os.path.join(d, "retro", "retro.md"), "w").close()
            self.assertEqual(work_path(d, "retro.md"), os.path.join(d, "retro", "retro.md"))


if __name__ == "__main__":
    unittest.main()
