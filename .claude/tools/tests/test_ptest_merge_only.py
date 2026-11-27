"""ptest merge-only files (configuration merge_only_tests) and the dev.sh gate split: --ext runs the four unit gates, --pytest the unit suite, no flag both; the full gate fits the script step timeout."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(TOOLS))
sys.path.insert(0, TOOLS)  # ptest runs this file alone: the tools dir is not on the path
import ptest  # noqa: E402  (import after the sys.path insert)
from pipeline import util  # noqa: E402  (import after the sys.path insert)
from pipeline.configuration import GATE_TIME_WARN_KEY, MERGE_ONLY_TESTS_DEFAULT, MERGE_ONLY_TESTS_KEY, project  # noqa: E402  (import after the sys.path insert)
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402  (import after the sys.path insert)

DEV_SH = os.path.join(REPO, "scripts", "dev.sh")
MERGE_ONLY = "test_lsp_e2e.py"  # the task-2 default: only the merge gate runs it


def touch_tests(names):
    """A flat tests dir holding empty files `names`; -> its path (removed by tracked_mkdtemp)."""
    d = tracked_mkdtemp()
    for n in names:
        Path(d, n).touch()
    return d


def dev_gate_block():
    """-> the `gate)` case block of scripts/dev.sh, up to the `clean)` line."""
    with open(DEV_SH, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("  gate)"))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("  clean)"))
    return "\n".join(lines[start:end])


class TestMergeOnlySelection(unittest.TestCase):
    def test_merge_only_file_left_out_of_a_glob(self):
        d = touch_tests(["test_a.py", MERGE_ONLY])
        self.assertEqual(ptest.select_files(d, ["test_*.py"], [MERGE_ONLY]), ["test_a.py"])

    def test_pattern_naming_the_merge_only_file_selects_it(self):
        d = touch_tests(["test_a.py", MERGE_ONLY])
        self.assertEqual(ptest.select_files(d, [MERGE_ONLY], [MERGE_ONLY]), [MERGE_ONLY])

    def test_pattern_given_as_repo_path_naming_it_selects_it(self):
        d = touch_tests(["test_a.py", MERGE_ONLY])
        self.assertIn(MERGE_ONLY, ptest.select_files(d, [f".claude/tools/tests/{MERGE_ONLY}"], [MERGE_ONLY]))

    def test_no_merge_only_list_keeps_every_file(self):
        d = touch_tests(["test_a.py", MERGE_ONLY])
        self.assertEqual(ptest.select_files(d, ["test_*.py"]), ["test_a.py", MERGE_ONLY])

    def test_merge_only_list_comes_from_configuration(self):
        for configured in ([], [MERGE_ONLY, "test_b.py"]):
            with self.subTest(configured=configured), mock.patch.object(ptest, "project", return_value={MERGE_ONLY_TESTS_KEY: configured}) as p:
                self.assertEqual(ptest.merge_only_tests(), configured)
                p.assert_called_once_with(ptest.DEFAULT_WORKSPACE)

    def test_empty_configured_list_runs_the_merge_only_file(self):
        d = touch_tests(["test_a.py", MERGE_ONLY])
        with mock.patch.object(ptest, "project", return_value={MERGE_ONLY_TESTS_KEY: []}):
            self.assertEqual(ptest.select_files(d, ["test_*.py"], ptest.merge_only_tests()), ["test_a.py", MERGE_ONLY])

    def test_default_merge_only_is_lsp_e2e(self):
        self.assertEqual(MERGE_ONLY_TESTS_DEFAULT, [MERGE_ONLY])
        self.assertEqual(project(tracked_mkdtemp())[MERGE_ONLY_TESTS_KEY], [MERGE_ONLY])


class TestGateSplit(unittest.TestCase):
    def test_usage_line_lists_gate(self):
        with open(DEV_SH, encoding="utf-8") as f:
            usage = f.read().splitlines()[2]
        self.assertIn("gate [--ext|--pytest]", usage)

    def test_gate_case_has_ext_and_pytest_arms(self):
        block = dev_gate_block()
        self.assertIn("--ext)", block)
        self.assertIn("--pytest)", block)

    def test_ext_arm_runs_the_four_unit_gates(self):
        ext = next(ln for ln in dev_gate_block().splitlines() if ln.strip().startswith("--ext)"))
        for sub in ("check", "dead", "sec", "ccn"):
            self.assertIn(f'dev.sh" {sub} ', ext)

    def test_pytest_arm_runs_the_unit_suite(self):
        pytest_arm = next(ln for ln in dev_gate_block().splitlines() if ln.strip().startswith("--pytest)"))
        self.assertIn('dev.sh" test ', pytest_arm)

    def test_no_flag_runs_both(self):
        block = dev_gate_block()
        self.assertIn("gate --ext ", block)
        self.assertIn("gate --pytest ", block)


class TestFullGateFitsStep(unittest.TestCase):
    def test_full_gate_warn_time_fits_the_script_timeout(self):
        full_s = project(tracked_mkdtemp())[GATE_TIME_WARN_KEY]["full"]
        self.assertLessEqual(full_s, util.script_timeout())


if __name__ == "__main__":
    unittest.main()
