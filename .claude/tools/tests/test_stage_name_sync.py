"""STAGE_NAME_RE (pipeline/util.py) must accept every phase dir name the plan tools create."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_files, util
from tests.support.tmpdirs import tracked_mkdtemp


class StageNameSync(unittest.TestCase):
    def test_numbered_phase_dirs_match(self):
        for nn in (1, 12):
            for slug in ("p3-course-a-tools", "finish", "build.v2", "a_b-c"):
                stage = f"feat/phases/{nn:02d}-{slug}"
                self.assertIsNotNone(util.STAGE_NAME_RE.fullmatch(stage), stage)

    def test_finish_dir_matches(self):
        self.assertTrue(plan_files.is_finish_dir(f"{3:02d}-finish"))
        self.assertIsNotNone(util.STAGE_NAME_RE.fullmatch(f"feat/phases/{3:02d}-finish"))

    def test_stage_root_finds_numbered_phase(self):
        root = tracked_mkdtemp()
        os.makedirs(os.path.join(root, ".work", "feat", "phases", "03-p3-course-a-tools"))
        self.assertEqual(util.stage_root(root, "feat/phases/03-p3-course-a-tools"), root)


if __name__ == "__main__":
    unittest.main()
