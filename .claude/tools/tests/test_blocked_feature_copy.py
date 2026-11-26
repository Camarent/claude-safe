"""write_blocked / clear_blocked: a phase stage dir writes its BLOCKED.md and the feature folder copy; a single-stage dir writes one file; the env preflight write goes through write_blocked."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import entry, preflight
from pipeline.configuration import BLOCKED_FILE, QRUN_TMP_MAX_PATH
from tests.support.tmpdirs import tracked_mkdtemp


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class WriteBlockedTest(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        self.feature = os.path.join(self.root, "feat")
        self.phase = os.path.join(self.feature, "phases", "01-first")

    def test_phase_dir_writes_both_copies(self):
        entry.write_blocked(self.phase, "# blocked msg\n")
        self.assertEqual(_read(os.path.join(self.phase, BLOCKED_FILE)), "# blocked msg\n")
        feature_copy = _read(os.path.join(self.feature, BLOCKED_FILE))
        self.assertEqual(feature_copy.splitlines()[0], "# Blocked in phase 01-first: see 01-first/BLOCKED.md")
        self.assertTrue(feature_copy.endswith("# blocked msg\n"))

    def test_single_stage_dir_writes_one_file(self):
        single = os.path.join(self.root, "solo")
        entry.write_blocked(single, "msg\n")
        self.assertEqual(_read(os.path.join(single, BLOCKED_FILE)), "msg\n")
        self.assertFalse(os.path.exists(os.path.join(self.root, BLOCKED_FILE)))


class ClearBlockedTest(unittest.TestCase):
    def test_clear_removes_both_copies(self):
        root = tracked_mkdtemp()
        feature = os.path.join(root, "feat")
        phase = os.path.join(feature, "phases", "02-next")
        entry.write_blocked(phase, "msg\n")
        entry.clear_blocked(phase)
        self.assertFalse(os.path.exists(os.path.join(phase, BLOCKED_FILE)))
        self.assertFalse(os.path.exists(os.path.join(feature, BLOCKED_FILE)))

    def test_clear_missing_is_fine(self):
        root = tracked_mkdtemp()
        entry.clear_blocked(os.path.join(root, "feat", "phases", "03-none"))
        entry.clear_blocked(os.path.join(root, "solo"))


class PreflightWritesThroughHelperTest(unittest.TestCase):
    def test_preflight_problem_lands_in_feature_copy(self):
        root = tracked_mkdtemp()
        phase = os.path.join(root, "feat", "phases", "01-first")
        os.makedirs(phase)
        long_base = "/" + "x" * QRUN_TMP_MAX_PATH  # a scratch TMPDIR over the limit: one env problem, no tool probe
        problems = preflight.run(root, phase, env={"QRUN_TMP_BASE": long_base})
        self.assertTrue(problems)
        self.assertTrue(os.path.isfile(os.path.join(root, "feat", BLOCKED_FILE)))
        self.assertTrue(_read(os.path.join(phase, BLOCKED_FILE)).startswith("# Env preflight: blocked"))


if __name__ == "__main__":
    unittest.main()
