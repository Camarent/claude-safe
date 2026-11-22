"""Acceptance reviewer for the MANUAL (`eyes:`) items: the brief, the verdict parse, and run() with a fake reviewer dispatch."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import acceptance
from pipeline.configuration import ACCEPTANCE_FILE, DONE, FAIL
from tests.test_acceptance import make_repo

PLAN = "# Plan: f\n\n## Acceptance checklist\n- cmd: `true` (exit 0)\n- eyes: button is blue\n- eyes: menu opens\n\n## Phases\n"
PASS_ALL = "MANUAL 1: PASS - button.css:4\nMANUAL 2: PASS - menu.js:12\n"


def ok_runner(cmd, cwd, timeout):
    return 0, "ok"


class BriefAndParseTest(unittest.TestCase):
    def test_brief_holds_diff_docs_and_the_verdict_format(self):
        brief = acceptance.manual_review_brief(["button is blue", "menu opens"], "feat/x", "main", ["README.md"])
        self.assertIn("git diff main...feat/x", brief)
        self.assertIn("`README.md`", brief)
        self.assertIn("`MANUAL n: PASS|FAIL|UNKNOWN - <evidence>`", brief)
        self.assertIn("1. button is blue", brief)
        self.assertIn("2. menu opens", brief)

    def test_verdicts_parse_and_a_missing_line_is_unknown(self):
        self.assertEqual(acceptance.manual_verdicts(PASS_ALL, 2), ["PASS", "PASS"])
        self.assertEqual(acceptance.manual_verdicts("MANUAL 2: FAIL - no\n", 2), ["UNKNOWN", "FAIL"])
        self.assertEqual(acceptance.manual_verdicts("MANUAL 1: PASS\nMANUAL 1: FAIL\n", 1), ["PASS"])
        self.assertEqual(acceptance.manual_verdicts("looks fine to me", 1), ["UNKNOWN"])


class ReviewerRunTest(unittest.TestCase):
    def test_run_dispatches_the_reviewer_once_with_the_manual_items(self):
        d = make_repo(PLAN)
        briefs = []

        def reviewer(brief):
            briefs.append(brief)
            return PASS_ALL

        v, text = acceptance.run(d, "f", "f", ok_runner, reviewer)
        self.assertEqual(v, DONE)
        self.assertEqual(len(briefs), 1)
        self.assertIn("git diff", briefs[0])
        self.assertIn("1. button is blue", briefs[0])
        md = open(os.path.join(d, ".work", "f", ACCEPTANCE_FILE), encoding="utf-8").read()
        self.assertIn("- MANUAL 1: PASS: button is blue", md)
        self.assertIn("- MANUAL 2: PASS: menu opens", md)
        self.assertIn("2 MANUAL item(s)", text)

    def test_missing_verdict_is_unknown_and_blocks(self):
        d = make_repo(PLAN)
        v, _ = acceptance.run(d, "f", "f", ok_runner, lambda brief: "MANUAL 1: PASS - ok\n")
        self.assertEqual(v, FAIL)
        md = open(os.path.join(d, ".work", "f", ACCEPTANCE_FILE), encoding="utf-8").read()
        self.assertIn("- MANUAL 2: UNKNOWN: menu opens", md)

    def test_fail_verdict_blocks_and_says_so(self):
        d = make_repo(PLAN)
        v, text = acceptance.run(d, "f", "f", ok_runner, lambda brief: "MANUAL 1: PASS\nMANUAL 2: FAIL - menu.js:3 missing\n")
        self.assertEqual(v, FAIL)
        self.assertIn("1 of 2 MANUAL item(s) not PASS", text)

    def test_no_reviewer_keeps_the_items_for_the_lead(self):
        d = make_repo(PLAN)
        v, _ = acceptance.run(d, "f", "f", ok_runner)
        self.assertEqual(v, DONE)
        md = open(os.path.join(d, ".work", "f", ACCEPTANCE_FILE), encoding="utf-8").read()
        self.assertIn("- MANUAL: button is blue", md)
