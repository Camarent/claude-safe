"""Report preflight: the dev report is checked against the task's Done line by script before the local pre-review and the paid reviewer. A FAIL
goes back to the dev as a free round (no review round, no reviewer dispatch)."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import report_preflight, steps
from pipeline.configuration import DONE, FAIL, WORK_DIR
from pipeline.steps_history import _failed_rounds
from pipeline.util import work_path
from test_task_gates import _SeqBase, dev, ev

PREFLIGHT_WHY = report_preflight.PREFLIGHT_WHY
PREREVIEW_WHY = "local pre-review (lp review); FAIL goes back to the dev"
DONE_LINE = "Done: C1 vulture and lizard output in the report; C4: mutation: skip the mutation rule, its test fails."


class PreflightRulesTest(unittest.TestCase):
    def test_tool_named_in_done_needs_its_output_in_the_report(self):
        msgs = report_preflight.check("Done: vulture clean", "all good")
        self.assertEqual(len(msgs), 1)
        self.assertIn("vulture", msgs[0])

    def test_tool_named_in_done_with_its_output_is_fine(self):
        self.assertEqual(report_preflight.check("Done: vulture clean", "vulture: 0 findings"), [])

    def test_mutation_asked_by_done_needs_a_named_mutation(self):
        msgs = report_preflight.check("Done: C4: mutation: skip the rule", "tests pass")
        self.assertEqual(len(msgs), 1)
        self.assertIn("mutation", msgs[0])

    def test_mutation_named_in_report_is_fine(self):
        self.assertEqual(report_preflight.check("Done: C4: mutation: skip the rule", "mutation: skipped the rule, test red"), [])

    def test_changed_test_without_a_named_mutation_is_named(self):
        msgs = report_preflight.check("Done: C1 fix it", "tests pass", ["x/test_a.py"])
        self.assertEqual(len(msgs), 1)
        self.assertIn("x/test_a.py", msgs[0])

    def test_changed_test_with_a_named_mutation_is_fine(self):
        self.assertEqual(report_preflight.check("Done: C1 fix it", "mutation: skipped the rule, test red", ["x/test_a.py"]), [])

    def test_changed_source_file_without_a_mutation_is_fine(self):
        self.assertEqual(report_preflight.check("Done: C1 fix it", "tests pass", ["x/steps.py"]), [])

    def test_diff_paths_reads_each_changed_file(self):
        diff = "diff --git a/x/test_a.py b/x/test_a.py\n+one\ndiff --git a/y/steps.py b/y/steps.py\n"
        self.assertEqual(report_preflight.diff_paths(diff), ["x/test_a.py", "y/steps.py"])

    def test_candidate_list_asked_by_done_needs_the_list_in_the_report(self):
        msgs = report_preflight.check("Done: a candidate list of three", "three items")
        self.assertEqual(len(msgs), 1)
        self.assertIn("candidate", msgs[0])

    def test_no_candidate_asked_means_no_candidate_message(self):
        self.assertEqual(report_preflight.check("Done: a plain fix", "three items"), [])

    def test_bare_file_name_cite_in_report_is_named(self):
        msgs = report_preflight.check("Done: fix it", "see steps.py:274 for the cite")
        self.assertEqual(len(msgs), 1)
        self.assertIn("steps.py", msgs[0])

    def test_full_path_cite_in_report_is_fine(self):
        self.assertEqual(report_preflight.check("Done: fix it", "see .claude/tools/pipeline/steps.py:274"), [])

    def test_done_line_is_read_from_the_brief(self):
        self.assertEqual(report_preflight.done_line("Title\nDone: one\nTest: x\nDone: two\n"), "Done: one\nDone: two")
        self.assertEqual(report_preflight.done_line("no done here"), "")


class PreflightStepTest(_SeqBase):
    def setUp(self):
        super().setUp()
        self.cfg(gates=[])
        self.brief = work_path(os.path.join(self.d, WORK_DIR, "s"), "task-1.md", create=True)
        Path(self.brief).write_text(f"Title\n{DONE_LINE}\n", encoding="utf-8")
        self.rep = Path(self.d, "d1.md")
        self.rep.write_text("C1 done, lizard clean, vulture clean.\n", encoding="utf-8")

    def dev_done(self):
        return [dev("d1"), ev("report", id="d1", role="dev", verdict=DONE, file=str(self.rep))]

    def preflight(self, i):
        return ev("dispatch", id=i, agent="script", role="script", model=None, why=PREFLIGHT_WHY)

    def preflight_report(self, i, verdict):
        f = Path(self.d, f"{i}.md")
        f.write_text(f"VERDICT: {verdict}\nmsg\n", encoding="utf-8")
        return ev("report", id=i, role="script", verdict=verdict, file=str(f))

    def test_dev_done_with_a_done_line_dispatches_the_preflight_before_the_pre_review(self):
        r = self.nxt(self.dev_done())
        self.assertEqual((r["role"], r["why"]), ("script", PREFLIGHT_WHY))
        self.assertIn("pipeline/report_preflight.py", r["prompt"])
        self.assertIn(str(self.rep), r["prompt"])

    def test_preflight_fail_sends_the_dev_back_free_with_no_reviewer(self):
        mine = [*self.dev_done(), self.preflight("p1"), self.preflight_report("p1", FAIL)]
        r = self.nxt(mine)
        self.assertEqual((r["action"], r["role"], r["free"]), ("dispatch", "dev", True))
        self.assertNotEqual(r["role"], "reviewer")
        self.assertEqual(_failed_rounds(mine), 0)

    def test_changed_test_in_the_diff_is_passed_to_the_preflight(self):
        diff = "diff --git a/.claude/tools/tests/test_a.py b/.claude/tools/tests/test_a.py\n+x\n"
        with mock.patch.object(steps, "_pre_review_diff", return_value=diff):
            r = self.nxt(self.dev_done())
        self.assertIn("--changed .claude/tools/tests/test_a.py", r["prompt"])

    def test_preflight_done_goes_on_to_the_pre_review(self):
        mine = [*self.dev_done(), self.preflight("p1"), self.preflight_report("p1", DONE)]
        self.assertEqual(self.nxt(mine)["why"], PREREVIEW_WHY)

    def test_no_done_line_in_the_brief_means_no_preflight(self):
        Path(self.brief).write_text("Title only\n", encoding="utf-8")
        self.assertEqual(self.nxt(self.dev_done())["why"], PREREVIEW_WHY)

    def test_preflight_fails_are_capped_then_the_preflight_is_skipped(self):
        mine = self.dev_done()
        for i in range(steps.PREREVIEW_FREE_ROUNDS):
            mine += [
                self.preflight(f"p{i}"),
                self.preflight_report(f"p{i}", FAIL),
                dev(f"e{i}", free=True),
                ev("report", id=f"e{i}", role="dev", verdict=DONE, file=str(self.rep)),
            ]
        self.assertEqual(self.nxt(mine)["why"], PREREVIEW_WHY)
