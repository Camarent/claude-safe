"""Same-wave candidates (`parallel:` warning) and the unchanged same-wave overlap rule (synthetic data only)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.configuration import WORK_DIR
from pipeline.plan_parallel import PARALLEL_WARN_PREFIX, parallel_candidates


class ParallelCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-parallel-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, WORK_DIR, "s")
        os.makedirs(self.d)

    def task(self, n, paths, wave, depends=()):
        with open(os.path.join(self.d, f"task-{n}.md"), "w", encoding="utf-8") as f:
            f.write(f"# T{n}\nPredicted paths: {paths}\n")
        return {"id": f"task-{n}", "wave": wave, "depends": [f"task-{x}" for x in depends]}

    def test_disjoint_paths_no_chain_flagged(self):
        ents = [self.task(1, "src/a.py", 2), self.task(2, "src/b.py", 3)]
        got = parallel_candidates(self.d, ents)
        self.assertEqual(
            got,
            [f"{PARALLEL_WARN_PREFIX}task-1 (wave 2) and task-2 (wave 3) touch disjoint paths and no depends chain orders them: could share a wave"],
        )
        self.assertEqual(plan_check.errors_of(got), [], "a parallel: line never fails a check")

    def test_direct_chain_not_flagged(self):
        ents = [self.task(1, "src/a.py", 2), self.task(2, "src/b.py", 3, depends=[1])]
        self.assertEqual(parallel_candidates(self.d, ents), [])

    def test_transitive_chain_not_flagged(self):
        ents = [
            self.task(1, "src/a.py", 2),
            self.task(2, "src/b.py", 3, depends=[1]),
            self.task(3, "src/c.py", 4, depends=[2]),
        ]
        self.assertEqual(parallel_candidates(self.d, ents), [])

    def test_overlapping_paths_not_flagged(self):
        ents = [self.task(1, "src/a.py", 2), self.task(2, "src/a.py", 3)]
        self.assertEqual(parallel_candidates(self.d, ents), [])

    def test_same_wave_not_flagged(self):
        ents = [self.task(1, "src/a.py", 2), self.task(2, "src/b.py", 2)]
        self.assertEqual(parallel_candidates(self.d, ents), [])

    def test_docs_task_disjoint_flagged(self):
        ents = [self.task(1, "docs/guide.md", 1), self.task(2, "src/b.py", 2)]
        got = parallel_candidates(self.d, ents)
        self.assertEqual(len(got), 1)
        self.assertIn("task-1 (wave 1) and task-2 (wave 2)", got[0])

    def test_same_wave_overlap_still_error(self):
        # merge-safety rule unchanged: same-wave overlap is an error, same-wave disjoint is not
        overlap = [self.task(1, "src/a.py", 2), self.task(2, "src/a.py", 2)]
        self.assertEqual(len(plan_check._overlap_problems(self.d, overlap)), 1)
        disjoint = [self.task(1, "src/a.py", 2), self.task(2, "src/b.py", 2)]
        self.assertEqual(plan_check._overlap_problems(self.d, disjoint), [])
