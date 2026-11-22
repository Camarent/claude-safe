"""`cs-plan amend --feature F --phase P --note TEXT`: note appended to the goal, given fields replaced, refusals, and the feature check after the write."""

import argparse
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan
from pipeline import plan_files as pf
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp


class PlanAmendTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.assertEqual(self.p("plan-init", "--feature", "f", "--goal", "big goal")[0], 0)
        plan = pf.plan_path(argparse.Namespace(dir=self.d, feature="f"))
        with open(plan, encoding="utf-8") as fh:
            txt = fh.read()
        with open(plan, "w", encoding="utf-8") as fh:  # the feature check needs one acceptance line
            fh.write(txt.replace("## Acceptance checklist\n", "## Acceptance checklist\n- cmd: true\n"))
        self.assertEqual(self.p("phase", "--feature", "f", "--name", "core", "--goal", "g", "--done-when", "true", "--paths", "src/core")[0], 0)
        self.assertEqual(
            self.p("phase", "--feature", "f", "--name", "ui", "--goal", "g2", "--done-when", "true", "--depends", "core", "--paths", "src/ui")[0], 0
        )

    def p(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", self.d])
        return rc, out.getvalue(), err.getvalue()

    def phase(self, name):
        return next(x for x in pf.read_phases(argparse.Namespace(dir=self.d, feature="f")) if x["dir"] == name)

    def test_note_is_appended_to_the_goal_and_the_check_runs(self):
        rc, out, _ = self.p("amend", "--feature", "f", "--phase", "core", "--note", "use the new API")
        self.assertEqual(rc, 0)
        self.assertIn("OK", out)  # the feature check ran and printed its result
        self.assertEqual(self.phase("01-core")["goal"], "g Amended: use the new API")

    def test_given_fields_are_replaced_and_the_others_kept(self):
        rc, _, _ = self.p("amend", "--feature", "f", "--phase", "ui", "--note", "n", "--goal", "new goal", "--paths", "src/ui2,src/ui3", "--depends", "core")
        self.assertEqual(rc, 0)
        ui = self.phase("02-ui")
        self.assertEqual(ui["goal"], "new goal Amended: n")
        self.assertEqual(ui["paths"], ["src/ui2", "src/ui3"])
        self.assertEqual(ui["done_when"], "true")
        self.assertEqual(ui["depends"], ["core"])  # stored as given

    def test_unknown_phase_is_refused(self):
        rc, _, err = self.p("amend", "--feature", "f", "--phase", "nope", "--note", "n")
        self.assertEqual(rc, 2)
        self.assertIn("unknown phase nope", err)

    def test_done_phase_is_refused(self):
        self.assertEqual(self.p("phase-done", "--feature", "f", "--name", "core")[0], 0)
        rc, _, err = self.p("amend", "--feature", "f", "--phase", "core", "--note", "n")
        self.assertEqual(rc, 2)
        self.assertIn("is done: amend refused", err)
        self.assertEqual(self.phase("01-core")["goal"], "g")  # nothing written

    def test_a_broken_done_when_fails_the_check_after_the_amend(self):
        # an unclosed quote is not a shell command (`sh -n` fails): the check run after the write must report it
        rc, out, _ = self.p("amend", "--feature", "f", "--phase", "core", "--note", "n", "--done-when", 'echo "unclosed')
        self.assertEqual(rc, 2)
        self.assertIn("01-core: done_when is not a shell command", out)
        self.assertEqual(self.phase("01-core")["done_when"], 'echo "unclosed')

    def test_a_path_only_done_when_naming_a_missing_file_fails_the_check(self):
        rc, out, _ = self.p("amend", "--feature", "f", "--phase", "core", "--note", "n", "--done-when", "cat src/nope.txt")
        self.assertEqual(rc, 2)
        self.assertIn("01-core: done_when needs src/nope.txt", out)
        self.assertEqual(self.phase("01-core")["done_when"], "cat src/nope.txt")


if __name__ == "__main__":
    unittest.main()
