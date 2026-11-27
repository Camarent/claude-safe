"""webtest_report: the report is written under the worktree with its HEAD stamp, and a report whose HEAD is not the branch HEAD is stale."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import webtest_report


class WriteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wt = Path(tmp.name, "wt")
        self.wt.mkdir()

    def test_writes_result_head_date_then_body(self):
        path = self.wt / "reports" / "report-webtest.md"
        webtest_report.write(str(path), "PASS", "abc1234", "2026-11-27T10:00:00", str(self.wt), "body text\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "RESULT: PASS\nHEAD: abc1234\nDATE: 2026-11-27T10:00:00\nbody text\n")

    def test_path_outside_the_worktree_is_refused(self):
        outside = Path(self.wt.parent, "elsewhere.md")
        with self.assertRaises(ValueError):
            webtest_report.write(str(outside), "PASS", "abc1234", "2026-11-27T10:00:00", str(self.wt))
        self.assertFalse(outside.exists())

    def test_dotdot_escape_is_refused(self):
        escape = str(self.wt / ".." / "escaped.md")
        with self.assertRaises(ValueError):
            webtest_report.write(escape, "FAIL", "abc1234", "2026-11-27T10:00:00", str(self.wt))
        self.assertFalse(Path(self.wt.parent, "escaped.md").exists())

    def test_result_outside_pass_fail_blocked_is_refused(self):
        path = self.wt / "report-webtest.md"
        with self.assertRaises(ValueError):
            webtest_report.write(str(path), "maybe", "abc1234", "2026-11-27T10:00:00", str(self.wt))
        self.assertFalse(path.exists())


class HeadLineTest(unittest.TestCase):
    def test_reads_the_head_line(self):
        self.assertEqual(webtest_report.head_line("RESULT: PASS\nHEAD: abc1234\n"), "abc1234")

    def test_missing_head_line_is_none(self):
        self.assertIsNone(webtest_report.head_line("RESULT: PASS\n"))

    def test_head_in_a_code_fence_is_not_the_line(self):
        text = "RESULT: PASS\n```\nHEAD: fake999\n```\n"
        self.assertIsNone(webtest_report.head_line(text))

    def test_head_mid_line_is_not_the_line(self):
        self.assertIsNone(webtest_report.head_line("note: the HEAD: abc1234 was old\n"))


class StaleTest(unittest.TestCase):
    def test_m25_failing_example_is_stale(self):
        text = "RESULT: PASS\nHEAD: abc1234\nDATE: 2026-11-27\n"
        self.assertEqual(webtest_report.stale(text, "def5678"), "stale webtest: report HEAD abc1234, branch HEAD def5678")

    def test_same_sha_short_against_full_is_not_stale(self):
        text = "RESULT: PASS\nHEAD: abc1234\n"
        self.assertIsNone(webtest_report.stale(text, "abc1234ffffffffffffffffffffffffffffffff"))

    def test_full_sha_in_report_short_on_branch_is_not_stale(self):
        text = "RESULT: PASS\nHEAD: abc1234ffffffffffffffffffffffffffffffff\n"
        self.assertIsNone(webtest_report.stale(text, "abc1234"))

    def test_one_char_head_is_stale_not_a_prefix_match(self):
        text = "RESULT: PASS\nHEAD: a\n"
        self.assertEqual(
            webtest_report.stale(text, "def5678ffffffffffffffffffffffffffffffff"),
            "stale webtest: report HEAD a, branch HEAD def5678ffffffffffffffffffffffffffffffff",
        )

    def test_six_char_prefix_is_stale_not_a_prefix_match(self):
        text = "RESULT: PASS\nHEAD: abc123\n"
        self.assertEqual(
            webtest_report.stale(text, "abc1234ffffffffffffffffffffffffffffffff"),
            "stale webtest: report HEAD abc123, branch HEAD abc1234ffffffffffffffffffffffffffffffff",
        )

    def test_missing_head_line_is_stale(self):
        self.assertEqual(webtest_report.stale("RESULT: PASS\n", "abc1234"), "stale webtest: no HEAD line")

    def test_decoy_head_in_fence_leaves_report_without_head(self):
        text = "RESULT: PASS\n```\nHEAD: abc1234\n```\n"
        self.assertEqual(webtest_report.stale(text, "abc1234"), "stale webtest: no HEAD line")


if __name__ == "__main__":
    unittest.main()
