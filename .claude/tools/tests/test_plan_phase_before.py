"""`cs-plan phase --before P`: insert ahead of P (also as the first phase), renumber, depends, refusals."""

import argparse
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan
from pipeline import plan_files as pf
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp


class PhaseBeforeTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.assertEqual(self.p("plan-init", "--feature", "f", "--goal", "big goal")[0], 0)
        self.assertEqual(self.p("phase", "--feature", "f", "--name", "core", "--goal", "g", "--done-when", "true", "--paths", "src/core")[0], 0)
        self.assertEqual(
            self.p("phase", "--feature", "f", "--name", "ui", "--goal", "g2", "--done-when", "true", "--depends", "core", "--paths", "src/ui")[0], 0
        )

    def p(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", self.d])
        return rc, out.getvalue(), err.getvalue()

    def phases(self):
        return pf.read_phases(argparse.Namespace(dir=self.d, feature="f"))

    def names(self):
        return [x["dir"] for x in self.phases()]

    def test_inserts_in_the_middle_and_renumbers_the_later_phase(self):
        rc, _, _ = self.p("phase", "--feature", "f", "--name", "docs", "--goal", "d", "--done-when", "true", "--paths", "docs", "--before", "ui")
        self.assertEqual(rc, 0)
        self.assertEqual(self.names(), ["01-core", "02-docs", "03-ui"])
        docs, ui = self.phases()[1], self.phases()[2]
        self.assertEqual(docs["depends"], ["01-core"])  # the phase before `ui`
        self.assertEqual(ui["depends"], ["core", "02-docs"])  # `ui` now waits for the new phase
        self.assertTrue(os.path.isdir(f"{self.d}/.work/f/phases/02-docs"))
        self.assertTrue(os.path.isdir(f"{self.d}/.work/f/phases/03-ui") and not os.path.exists(f"{self.d}/.work/f/phases/02-ui"))

    def test_inserts_as_the_first_phase_as_00(self):
        rc, _, _ = self.p("phase", "--feature", "f", "--name", "docs", "--goal", "d", "--done-when", "true", "--paths", "docs", "--before", "core")
        self.assertEqual(rc, 0)
        self.assertEqual(self.names(), ["00-docs", "01-core", "02-ui"])
        docs, core = self.phases()[0], self.phases()[1]
        self.assertEqual(docs["depends"], [])  # none before the first phase
        self.assertEqual(core["depends"], ["00-docs"])  # `core` waits for the new phase
        self.assertTrue(os.path.isdir(f"{self.d}/.work/f/phases/00-docs"))

    def test_refuses_when_the_phase_before_has_started(self):
        Path(f"{self.d}/.work/f/phases/02-ui/TASKS.md").write_text("x", encoding="utf-8")
        rc, _, err = self.p("phase", "--feature", "f", "--name", "late", "--goal", "g", "--done-when", "true", "--paths", "late", "--before", "ui")
        self.assertEqual(rc, 2)
        self.assertIn("has started", err)
        self.assertEqual(self.names(), ["01-core", "02-ui"])

    def test_unknown_before_phase_is_refused(self):
        rc, _, err = self.p("phase", "--feature", "f", "--name", "docs", "--goal", "d", "--done-when", "true", "--paths", "docs", "--before", "missing")
        self.assertEqual(rc, 2)
        self.assertIn("unknown phase missing", err)
        self.assertEqual(self.names(), ["01-core", "02-ui"])

    def test_before_and_after_together_is_an_error(self):
        rc, _, err = self.p(
            "phase", "--feature", "f", "--name", "docs", "--goal", "d", "--done-when", "true", "--paths", "docs", "--before", "ui", "--after", "core"
        )
        self.assertEqual(rc, 2)
        self.assertIn("give one", err)
        self.assertEqual(self.names(), ["01-core", "02-ui"])
