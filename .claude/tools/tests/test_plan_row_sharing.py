"""Row-level wave sharing, registry/size warnings and Affected tests (synthetic data only)."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs, plan_check, plan_files, plan_rules
from pipeline.configuration import WORK_DIR


class _Run:
    def __init__(self, stage, d):
        self.stage = stage
        self.dir = d


class RowSharingTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-rows-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, WORK_DIR, "s")
        os.makedirs(self.d)

    def task(self, n, paths, wave, extra=""):
        with open(os.path.join(self.d, f"task-{n}.md"), "w", encoding="utf-8") as f:
            f.write(f"# T{n}\nPredicted paths: {paths}\n{extra}")
        return {"id": f"task-{n}", "wave": wave}

    def clash(self, p1, p2):
        return plan_check._overlap_problems(self.d, [self.task(1, p1, 1), self.task(2, p2, 1)])

    def test_rows_rows_jsonl_share(self):
        self.assertEqual(self.clash("data/k.jsonl (rows), a.py", "data/k.jsonl (rows)"), [])

    def test_refused_combinations(self):
        for p1, p2 in [
            ("data/k.jsonl (rows)", "data/k.jsonl (mass)"),
            ("data/k.jsonl (mass)", "data/k.jsonl (mass)"),
            ("data/k.jsonl (rows)", "data/k.jsonl"),
            ("data/k.jsonl", "data/k.jsonl"),
            ("data/k.json (rows)", "data/k.json (rows)"),
            ("data (rows)", "data/k.jsonl (rows)"),
        ]:
            with self.subTest(p1=p1, p2=p2):
                self.assertEqual(len(self.clash(p1, p2)), 1)

    def test_helpers_return_plain_paths(self):
        self.task(1, '`a.jsonl (rows)`, "b, c.tsv (mass)", d.py', 1)
        self.assertEqual(plan_files.task_paths(self.d, "task-1"), ["a.jsonl", "b, c.tsv", "d.py"])
        self.assertEqual(plan_files.task_path_modes(self.d, "task-1"), [("a.jsonl", "rows"), ("b, c.tsv", "mass"), ("d.py", "")])

    def test_shared_silent_for_rows_only(self):
        ents = [self.task(i, "data/k.jsonl (rows)", 1) for i in (1, 2, 3)]
        self.assertEqual(plan_check.shared_warnings(self.d, ents), [])
        ents[2] = self.task(3, "data/k.jsonl", 2)
        self.assertTrue(plan_check.shared_warnings(self.d, ents)[0].startswith("shared:"))

    def test_registry_is_error_not_warning(self):
        ents = [self.task(i, "tests/gloss-edited-ids.js", i) for i in (1, 2, 3)]
        self.assertEqual(plan_check.shared_warnings(self.d, ents), [])
        got = plan_check.shared_file_errors(plan_check._listings(self.d, ents))
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("shared-file:"))
        self.assertEqual(plan_check.errors_of(got), got)

    def test_size_warning(self):
        self.task(1, "a.jsonl (rows)", 1, "Goal: fix 546 rows by hand\n")
        got = plan_rules.check_task_size(self.d, "task-1")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("size:") and "split by id range" in got[0])
        self.assertEqual(plan_check.errors_of(got), [])
        self.task(2, "a.jsonl", 1, "Goal: fix 60 rows\n")
        self.assertEqual(plan_rules.check_task_size(self.d, "task-2"), [])
        self.task(3, "a.jsonl", 1, "Type: report\nGoal: list 500 rows\n")
        self.assertEqual(plan_rules.check_task_size(self.d, "task-3"), [])

    def test_affected_tests_in_briefs(self):
        self.task(1, "a.py", 1, "Affected tests: tests/test_a.py\n")
        self.assertEqual(plan_files.task_affected_tests(open(os.path.join(self.d, "task-1.md")).read()), "tests/test_a.py")
        a = _Run("s", self.root)
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=None):
            dev = briefs.prompt_for(a, {"id": "task-1"}, "dev", None, "x1")
            rev = briefs.prompt_for(a, {"id": "task-1"}, "reviewer", None, "x2")
            self.task(2, "a.py", 1)
            plain = briefs.prompt_for(a, {"id": "task-2"}, "dev", None, "x3")
        self.assertIn("Affected tests (hint, may be incomplete): tests/test_a.py. Check them first; update them if the new behavior is intended.", dev)
        self.assertIn("tests/test_a.py", dev)
        self.assertIn("Check that test changes match the task's Done and do not weaken a check.", rev)
        self.assertNotIn("Affected tests (hint", plain)

    def test_dry_run_layout(self):
        """4 tasks on one jsonl: two rows, one mass, one rows after the mass -> waves 1,2,3 ... mass alone."""
        ents = [self.task(1, "d.jsonl (rows)", 1), self.task(2, "d.jsonl (rows)", 1), self.task(3, "d.jsonl (mass)", 2), self.task(4, "d.jsonl (rows)", 3)]
        self.assertEqual(plan_check._overlap_problems(self.d, ents), [])
        bad = [ents[0], ents[1], {**ents[2], "wave": 1}]
        self.assertEqual(len(plan_check._overlap_problems(self.d, bad)), 2)


if __name__ == "__main__":
    unittest.main()
