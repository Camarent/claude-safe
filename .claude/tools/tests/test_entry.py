"""pipeline.entry: the status-line formatter, the stage name, the run flags, the open-phase resolver and the exit-code table."""

import argparse
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import entry
from pipeline import plan_files as pf
from pipeline.configuration import EXIT_BLOCKED, EXIT_CODES, EXIT_LIMIT, EXIT_OK


class StatusLineTest(unittest.TestCase):
    def test_blocked_line_matches_old_print(self):
        self.assertEqual(entry.blocked_line("/w/F", "why"), "BLOCKED, back to lead (also in /w/F/BLOCKED.md):\nwhy")

    def test_limit_line_matches_old_print(self):
        self.assertEqual(entry.limit_line("max-waves 3"), "limit: max-waves 3")

    def test_banner_and_done_line(self):
        self.assertEqual(entry.banner("planning F/phases/01-a"), "== planning F/phases/01-a")
        self.assertEqual(entry.done_line("all good"), "done: all good")


class NameOfStageTest(unittest.TestCase):
    def test_phase_stage_gives_feature(self):
        self.assertEqual(entry.name_of_stage("F/phases/01-x"), "F")

    def test_plain_name_unchanged(self):
        self.assertEqual(entry.name_of_stage("S"), "S")


class RunFlagsTest(unittest.TestCase):
    def test_flags_parse(self):
        p = argparse.ArgumentParser()
        entry.add_run_flags(p)
        a = p.parse_args(["--max-waves", "2", "--max-budget-usd", "1.5", "--dry-run", "--stub", "unit-x:dev=BLOCKED", "--allow-dirty"])
        self.assertEqual((a.max_waves, a.max_dispatches, a.max_sessions, a.max_budget_usd), (2, None, None, 1.5))
        self.assertTrue(a.dry_run)
        self.assertTrue(a.allow_dirty)
        self.assertEqual(a.stub, ["unit-x:dev=BLOCKED"])


class ResolveOpenPhaseTest(unittest.TestCase):
    def _feature(self, d, rows):
        feat = os.path.join(d, ".work", "F")
        for n in ("01-a", "02-b"):
            os.makedirs(os.path.join(feat, "phases", n))
        with open(os.path.join(feat, "Plan.md"), "w") as f:
            f.write("## Phases\n" + pf.PHASE_HEAD + rows)

    def test_first_open_phase(self):
        with tempfile.TemporaryDirectory() as d:
            self._feature(d, "| 01-a | g | true | | | done | 1 |\n| 02-b | g | true | 01-a | | todo | 1 |\n")
            self.assertEqual(entry.resolve_open_phase(d, "F"), "F/phases/02-b")

    def test_phase_path_and_plain_name_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            self._feature(d, "| 01-a | g | true | | | todo | 1 |\n")
            self.assertEqual(entry.resolve_open_phase(d, "F/phases/01-a"), "F/phases/01-a")
            self.assertEqual(entry.resolve_open_phase(d, "S"), "S")

    def test_all_done_raises(self):
        with tempfile.TemporaryDirectory() as d:
            self._feature(d, "| 01-a | g | true | | | done | 1 |\n| 02-b | g | true | 01-a | | done | 1 |\n")
            with self.assertRaisesRegex(ValueError, "F"):
                entry.resolve_open_phase(d, "F")


class ExitCodesTest(unittest.TestCase):
    def test_table_pins_codes(self):
        self.assertEqual((EXIT_OK, EXIT_LIMIT, EXIT_BLOCKED), (0, 3, 4))
        self.assertEqual(set(EXIT_CODES), {0, 3, 4})


if __name__ == "__main__":
    unittest.main()
