"""pipeline.run.Run: built once from args, frozen, keeps the Namespace attribute names."""

import argparse
import dataclasses
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.run import Run


class RunTest(unittest.TestCase):
    def test_from_args_copies_known_fields_and_defaults_the_rest(self):
        a = argparse.Namespace(dir="d", stage="s", max_waves=3, max_budget_usd=None, fn=print, cmd="run", stub=["t:dev=done,fail"])
        r = Run.from_args(a)
        self.assertEqual((r.dir, r.stage, r.max_waves, r.max_budget_usd, r.dry_run, r.feature), ("d", "s", 3, None, False, ""))
        self.assertEqual(r.stub_plan, {("t", "dev"): ["DONE", "FAIL"]})

    def test_frozen_and_of_is_idempotent(self):
        r = Run.of(SimpleNamespace(dir="d", stage="s"))
        self.assertIs(Run.of(r), r)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            r.stage = "x"  # type: ignore[misc]

    def test_at_copies_with_changes(self):
        r = Run(dir="d", stage="s", dry_run=True)
        r2 = r.at(dir="tmp")
        self.assertEqual((r2.dir, r2.stage, r2.dry_run, r.dir), ("tmp", "s", True, "d"))


if __name__ == "__main__":
    unittest.main()
