"""plan_review core: verdict parse, model choice, cover limit, one fix round (fake dispatch and fix, no sessions)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_review as pr  # import after the sys.path insert above
from pipeline.configuration import (  # import after the sys.path insert above
    PLAN_EXTRA_TASKS_ALLOWED,
    PLAN_REVIEW_MODEL_HEAVY,
    PLAN_REVIEW_MODEL_LIGHT,
    PLAN_REVIEW_SMALL_MAX_TASKS,
)

DONE_REPORT = "all fine\nVERDICT: DONE"
FAIL_REPORT = "GAP: phase 2 has no done-when\nGAP: task 3 has no test\nVERDICT: FAIL"


class Recorder:
    """Fake dispatch and fix that record their calls and return canned values."""

    def __init__(self, report="", fix_error=None, dispatch_error=None):
        self.report = report
        self.fix_error = fix_error
        self.dispatch_error = dispatch_error
        self.dispatched = []
        self.fixed = []

    def dispatch(self, model, kind):
        self.dispatched.append((model, kind))
        if self.dispatch_error:
            raise self.dispatch_error
        return self.report

    def fix(self, report_text):
        self.fixed.append(report_text)
        if self.fix_error:
            raise self.fix_error


class ParseVerdictTest(unittest.TestCase):
    def test_done_fail_blocked(self):
        self.assertEqual(pr.parse_verdict(DONE_REPORT), "DONE")
        self.assertEqual(pr.parse_verdict(FAIL_REPORT), "FAIL")
        self.assertEqual(pr.parse_verdict("VERDICT: BLOCKED"), "BLOCKED")

    def test_last_verdict_line_wins(self):
        self.assertEqual(pr.parse_verdict("VERDICT: FAIL\nmore\nVERDICT: DONE"), "DONE")

    def test_decoy_verdict_inside_prose_is_ignored(self):
        self.assertEqual(pr.parse_verdict("I will write a VERDICT: DONE line at the end"), "")
        self.assertEqual(pr.parse_verdict("the word VERDICT: FAIL is not a verdict here"), "")

    def test_no_verdict_gives_empty(self):
        self.assertEqual(pr.parse_verdict("no verdict in this report"), "")
        self.assertEqual(pr.parse_verdict(""), "")
        self.assertEqual(pr.parse_verdict(None), "")

    def test_garbled_verdict_gives_empty(self):
        self.assertEqual(pr.parse_verdict("VERDICT: MAYBE"), "")

    def test_gap_lines_and_gap_tasks(self):
        self.assertEqual(pr.parse_gaps(FAIL_REPORT), ["GAP: phase 2 has no done-when", "GAP: task 3 has no test"])
        self.assertEqual(pr.parse_gap_tasks("GAP-TASKS: 4\nGAP: x"), 4)
        self.assertEqual(pr.parse_gap_tasks("GAP: a\nGAP: b\nVERDICT: FAIL"), 2)
        self.assertEqual(pr.parse_gap_tasks("nothing"), 0)


class ReviewModelTest(unittest.TestCase):
    def test_phase_and_task_are_light(self):
        self.assertEqual(pr.review_model(pr.KIND_PHASE), PLAN_REVIEW_MODEL_LIGHT)
        self.assertEqual(pr.review_model(pr.KIND_TASK), PLAN_REVIEW_MODEL_LIGHT)

    def test_cover_is_heavy(self):
        self.assertEqual(pr.review_model(pr.KIND_COVER), PLAN_REVIEW_MODEL_HEAVY)

    def test_single_boundary_by_task_count(self):
        self.assertEqual(pr.review_model(pr.KIND_SINGLE, PLAN_REVIEW_SMALL_MAX_TASKS), PLAN_REVIEW_MODEL_LIGHT)
        self.assertEqual(pr.review_model(pr.KIND_SINGLE, PLAN_REVIEW_SMALL_MAX_TASKS + 1), PLAN_REVIEW_MODEL_HEAVY)

    def test_single_small_is_haiku_and_big_is_sonnet(self):
        self.assertEqual(pr.review_model(pr.KIND_SINGLE, 7), "haiku")
        self.assertEqual(pr.review_model(pr.KIND_SINGLE, 8), "sonnet")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            pr.review_model("nope")


class CoverBlockedTest(unittest.TestCase):
    def test_equal_is_allowed(self):
        self.assertFalse(pr.cover_blocked(20, 10, 20, extra=10))

    def test_over_limit_plus_extra_is_blocked(self):
        self.assertTrue(pr.cover_blocked(20, 11, 20, extra=10))

    def test_default_extra_comes_from_settings(self):
        limit = 20
        self.assertFalse(pr.cover_blocked(limit, PLAN_EXTRA_TASKS_ALLOWED, limit))
        self.assertTrue(pr.cover_blocked(limit, PLAN_EXTRA_TASKS_ALLOWED + 1, limit))


class RunReviewTest(unittest.TestCase):
    def test_done_has_no_fix_call(self):
        rec = Recorder(DONE_REPORT)
        res = pr.run_review(pr.KIND_PHASE, rec.dispatch, rec.fix)
        self.assertEqual((res.verdict, res.rounds, res.gaps), ("DONE", 0, []))
        self.assertEqual(rec.fixed, [])
        self.assertEqual(rec.dispatched, [(PLAN_REVIEW_MODEL_LIGHT, "phase")])

    def test_fail_calls_fix_once_with_report_and_no_rereview(self):
        rec = Recorder(FAIL_REPORT)
        res = pr.run_review(pr.KIND_TASK, rec.dispatch, rec.fix)
        self.assertEqual(res.verdict, "FAIL")
        self.assertEqual(res.rounds, pr.PLAN_REVIEW_ROUNDS)
        self.assertEqual(rec.fixed, [FAIL_REPORT])
        self.assertEqual(len(rec.dispatched), 1)
        self.assertEqual(res.gaps, ["GAP: phase 2 has no done-when", "GAP: task 3 has no test"])

    def test_fail_with_zero_rounds_has_no_fix_call(self):
        rec = Recorder(FAIL_REPORT)
        with mock.patch.object(pr, "PLAN_REVIEW_ROUNDS", 0):
            res = pr.run_review(pr.KIND_TASK, rec.dispatch, rec.fix)
        self.assertEqual((res.verdict, res.rounds), ("FAIL", 0))
        self.assertEqual(rec.fixed, [])
        self.assertEqual(len(res.gaps), 2)

    def test_blocked_verdict_on_cover_has_no_fix(self):
        rec = Recorder("VERDICT: BLOCKED")
        res = pr.run_review(pr.KIND_COVER, rec.dispatch, rec.fix, n_tasks=5, limit=20)
        self.assertEqual(res.verdict, "BLOCKED")
        self.assertEqual(rec.fixed, [])

    def test_blocked_by_limit_on_cover_has_no_fix(self):
        rec = Recorder("GAP-TASKS: 50\nGAP: many\nVERDICT: FAIL")
        res = pr.run_review(pr.KIND_COVER, rec.dispatch, rec.fix, n_tasks=20, limit=20)
        self.assertEqual(res.verdict, "BLOCKED")
        self.assertEqual(rec.fixed, [])

    def test_cover_within_limit_fails_then_fixes(self):
        rec = Recorder("GAP-TASKS: 2\nGAP: a\nGAP: b\nVERDICT: FAIL")
        res = pr.run_review(pr.KIND_COVER, rec.dispatch, rec.fix, n_tasks=20, limit=20)
        self.assertEqual((res.verdict, res.rounds, res.model), ("FAIL", 1, PLAN_REVIEW_MODEL_HEAVY))
        self.assertEqual(len(rec.fixed), 1)

    def test_garbled_report_gives_empty_verdict_and_no_fix(self):
        rec = Recorder("garbage output, no verdict line")
        res = pr.run_review(pr.KIND_PHASE, rec.dispatch, rec.fix)
        self.assertEqual((res.verdict, res.rounds), ("", 0))
        self.assertEqual(rec.fixed, [])

    def test_dispatch_exception_gives_error_and_no_raise(self):
        rec = Recorder(dispatch_error=RuntimeError("session died"))
        res = pr.run_review(pr.KIND_PHASE, rec.dispatch, rec.fix)
        self.assertEqual(res.verdict, "ERROR")
        self.assertEqual(rec.fixed, [])

    def test_fix_exception_gives_error_and_no_raise(self):
        rec = Recorder(FAIL_REPORT, fix_error=RuntimeError("fix died"))
        res = pr.run_review(pr.KIND_TASK, rec.dispatch, rec.fix)
        self.assertEqual(res.verdict, "ERROR")
        self.assertEqual(len(rec.fixed), 1)

    def test_single_model_follows_task_count(self):
        rec = Recorder(DONE_REPORT)
        pr.run_review(pr.KIND_SINGLE, rec.dispatch, rec.fix, n_tasks=PLAN_REVIEW_SMALL_MAX_TASKS + 1)
        self.assertEqual(rec.dispatched, [(PLAN_REVIEW_MODEL_HEAVY, "single")])


class BuildPromptTest(unittest.TestCase):
    def test_names_request_plan_tasks_and_report(self):
        text = pr.build_prompt(pr.KIND_PHASE, "feat", ".work/feat/review-phase.md")
        for name in (".work/feat/request.md", ".work/feat/Plan.md", ".work/feat/TASKS.md", ".work/feat/review-phase.md"):
            self.assertIn(name, text)
        self.assertIn("GAP:", text)
        self.assertIn("VERDICT:", text)

    def test_cover_asks_for_gap_tasks_line(self):
        self.assertIn("GAP-TASKS: n", pr.build_prompt(pr.KIND_COVER, "feat", "r.md"))
        self.assertNotIn("GAP-TASKS", pr.build_prompt(pr.KIND_TASK, "feat", "r.md"))

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            pr.build_prompt("nope", "feat", "r.md")


if __name__ == "__main__":
    unittest.main()
