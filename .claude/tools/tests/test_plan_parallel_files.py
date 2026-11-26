"""Same-file tasks never share a wave (synthetic task files only): plan_check's same-wave overlap rule and parallel_candidates both leave them alone."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.configuration import WORK_DIR
from pipeline.plan_parallel import parallel_candidates


class SameFileWaveTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-samefile-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, WORK_DIR, "s")
        os.makedirs(self.d)

    def task(self, n, paths, wave):
        with open(os.path.join(self.d, f"task-{n}.md"), "w", encoding="utf-8") as f:
            f.write(f"# T{n}\nPredicted paths: {paths}\n")
        return {"id": f"task-{n}", "wave": wave, "depends": []}

    def test_same_file_same_wave_is_a_problem(self):
        ents = [self.task(1, "src/shared.py", 2), self.task(2, "src/shared.py", 2)]
        probs = plan_check._overlap_problems(self.d, ents)
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("share paths in wave 2", probs[0])

    def test_same_file_different_waves_is_not_a_parallel_candidate(self):
        ents = [self.task(1, "src/shared.py", 2), self.task(2, "src/shared.py", 3)]
        self.assertEqual(parallel_candidates(self.d, ents), [])

    def test_disjoint_same_wave_is_not_a_problem(self):
        ents = [self.task(1, "src/a.py", 2), self.task(2, "src/b.py", 2)]
        self.assertEqual(plan_check._overlap_problems(self.d, ents), [])
