"""A test that makes a branch must not switch the real checkout: run the phase-driver scenario with the temp dir inside the real checkout."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ptest
from tests import test_feature
from tests.support.tmpdirs import tracked_tmpdir

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main_dir():
    """The main checkout: the folder owning the shared .git, also when this runs from a worktree."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=TOOLS, capture_output=True, text=True, check=True
    ).stdout.strip()
    return os.path.dirname(common)


class NoGitScenarioKeepsRealCheckout(unittest.TestCase):
    def test_feature_driver_inside_checkout_leaves_branch_state(self):
        # the temp dirs of the driver tests sit in the real checkout; before the fix their plan-init switched it to stage/F
        # under .work/ (git-ignored): the driver's dirty-checkout guard passes there, so a leak is not masked by it
        ignored = os.path.join(main_dir(), ".work")
        os.makedirs(ignored, exist_ok=True)
        inside = tracked_tmpdir(dir=ignored)
        before = ptest.main_branch_state()
        self.addCleanup(self.restore, before[0])
        with mock.patch.object(tempfile, "tempdir", inside.name):
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_feature.FeatureDriver)
            result = unittest.TestResult()
            suite.run(result)
        self.assertEqual(ptest.main_branch_state(), before)
        self.assertEqual(result.errors + result.failures, [])

    @staticmethod
    def restore(branch):
        if branch and ptest.main_branch_state()[0] != branch:
            subprocess.run(["git", "switch", "-q", branch], cwd=main_dir(), check=False)


if __name__ == "__main__":
    unittest.main()
