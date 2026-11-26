"""Table-driven: every stage-dir resolver keeps rejecting and accepting what it did before the merge into pipeline/stagedir.py."""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ops_steps
from pipeline import plan_check, plan_files, util
from tests.support.tmpdirs import tracked_mkdtemp


class StageResolveTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tracked_mkdtemp())
        for rel in (".work/s", ".work/feat/phases/01-a", "repo/.git"):
            os.makedirs(os.path.join(self.root, rel))
        open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8").write("{}")
        self.nested = os.path.join(self.root, "repo")

    def test_ops_steps_strict(self):
        w = os.path.join(self.root, ".work")
        for stage in ("s", "feat/phases/01-a", "missing", "a/b"):
            self.assertEqual(ops_steps.stage_dir(self.root, stage), os.path.join(w, stage), stage)
        for bad in ("", "/", "..", "../x", "a/../b", ".", "./a", "/abs/path"):
            with self.assertRaises(ValueError, msg=bad):
                ops_steps.stage_dir(self.root, bad)

    def test_ops_steps_makes_relative_dir_absolute(self):
        rel = os.path.relpath(self.root)
        self.assertEqual(ops_steps.stage_dir(rel, "s"), os.path.join(self.root, ".work", "s"))

    def test_plan_files_direct_then_phases_then_direct_path(self):
        w = os.path.join(self.root, ".work")

        def sd(stage):
            return plan_files.stage_dir(SimpleNamespace(dir=self.root, stage=stage))

        self.assertEqual(sd("s"), os.path.join(w, "s"))
        self.assertEqual(sd("feat/phases/01-a"), os.path.join(w, "feat/phases/01-a"))
        self.assertEqual(sd("01-a"), os.path.join(w, "feat", "phases", "01-a"))
        self.assertEqual(sd("missing"), os.path.join(w, "missing"))
        self.assertEqual(sd("../x"), os.path.join(w, "../x"))  # lenient: no name check
        self.assertEqual(plan_files.stage_dir(SimpleNamespace(dir="rel", stage="zz")), os.path.join("rel", ".work", "zz"))

    def test_stage_root_walk_up_and_rejects(self):
        self.assertEqual(util.stage_root(self.root, "s"), self.root)
        self.assertEqual(util.stage_root(self.root, "feat/phases/01-a"), self.root)
        self.assertEqual(util.stage_root(self.nested, "s"), self.root)  # nested repo
        self.assertIsNone(util.stage_root(self.nested, "missing"))
        for bad in ("", "..", "../s", "/abs", "a/../s", "s/", "a b"):
            self.assertIsNone(util.stage_root(self.root, bad), bad)

    def test_plan_check_work_root(self):
        w = os.path.join(self.root, ".work")
        for d in (w, os.path.join(w, "s"), os.path.join(w, "feat", "phases", "01-a")):
            self.assertEqual(plan_check._workspace(d), self.root, d)
            self.assertEqual(plan_check._work_root(d), w, d)


if __name__ == "__main__":
    unittest.main()
