"""Phase path overlap: rejected only when no depends chain orders the two phases (cs-plan phase and plan_check)."""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan, plan_check
from pipeline import plan_files as pf
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp


def phase(dir_, paths, depends=()):
    return {"dir": dir_, "depends": list(depends), "paths": list(paths)}


class PhaseOrderedTest(unittest.TestCase):
    def setUp(self):
        self.phases = [phase("01-core", ["src/shared"]), phase("02-ui", ["src/ui"], ["core"]), phase("03-docs", ["docs"], ["02-ui"])]

    def test_direct_and_transitive_chains_are_ordered(self):
        self.assertTrue(pf.phases_ordered(self.phases, "02-ui", "01-core"))
        self.assertTrue(pf.phases_ordered(self.phases, "03-docs", "01-core"))
        self.assertTrue(pf.phases_ordered(self.phases, "01-core", "03-docs"))  # either direction

    def test_unrelated_phases_are_not_ordered(self):
        free = [*self.phases, phase("04-lint", ["lint"])]
        self.assertFalse(pf.phases_ordered(free, "04-lint", "01-core"))
        self.assertFalse(pf.phases_ordered(free, "04-lint", "02-ui"))

    def test_names_match_like_phase_ref(self):
        self.assertTrue(pf.phases_ordered(self.phases, "ui", "core"))  # suffix names match
        self.assertFalse(pf.phases_ordered(self.phases, "ui", "missing"))  # unknown name: never ordered


class PhaseLinkProblemsTest(unittest.TestCase):
    def test_shared_paths_ordered_by_depends_are_accepted(self):
        core = phase("01-core", ["src/shared"])
        self.assertEqual(plan_check._phase_link_problems(phase("02-ui", ["src/shared/briefs.py"], ["core"]), [core]), [])

    def test_shared_paths_without_chain_are_reported(self):
        core = phase("01-core", ["src/shared"])
        probs = plan_check._phase_link_problems(phase("02-ui", ["src/shared/briefs.py"]), [core])
        self.assertEqual(probs, ["02-ui and 01-core share paths: src/shared/briefs.py / src/shared"])


class PhaseCommandTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.assertEqual(self.p("plan-init", "--feature", "f", "--goal", "g")[0], 0)

    def p(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", self.d])
        return rc, out.getvalue(), err.getvalue()

    def phase(self, name, paths, depends=""):
        argv = ["phase", "--feature", "f", "--name", name, "--goal", "g", "--done-when", "true", "--paths", paths]
        if depends:
            argv += ["--depends", depends]
        return self.p(*argv)

    def test_chained_overlap_is_accepted(self):
        self.assertEqual(self.phase("core", "src/shared")[0], 0)
        self.assertEqual(self.phase("ui", "src/shared/briefs.py", "core")[0], 0)  # shared briefs.py, ordered by depends
        self.assertEqual(self.phase("report", "src/shared/x", "ui")[0], 0)  # transitive: report -> ui -> core

    def test_same_paths_without_chain_are_rejected(self):
        self.assertEqual(self.phase("core", "src/shared")[0], 0)
        rc, _, err = self.phase("ui", "src/shared/briefs.py")
        self.assertEqual(rc, 2)
        self.assertIn("paths overlap with phase 01-core", err)

    def test_overlap_with_unordered_phase_is_still_rejected(self):
        self.assertEqual(self.phase("core", "src/shared")[0], 0)
        self.assertEqual(self.phase("ui", "src/shared/a", "core")[0], 0)
        rc, _, err = self.phase("lint", "src/shared/b")  # ordered with ui, not with core: core still overlaps
        self.assertEqual(rc, 2)
        self.assertIn("paths overlap with phase 01-core", err)


if __name__ == "__main__":
    unittest.main()
