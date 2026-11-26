"""Brief tuning: the lead code slices are capped by the task's Pattern, the rework brief carries the findings verbatim, a docs-only review gets a turn cap, and the Opus plan rerun amends the Sonnet draft."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs, plan_run
from pipeline.configuration import (
    ENV_NO_LEADS,
    ESCALATE_DRAFT_CHARS,
    LEAD_SLICE_CHARS,
    LEAD_SLICE_CHARS_WITH_PATTERN,
    PLAN_FILE,
    REVIEW_DOCS_MAX_TURNS,
    TASKS_FILE,
    WORK_DIR,
)

TASK_WITH_PATTERN = "Pattern: src/mod.py\nPredicted paths: src/mod.py\nDone: x\n"
TASK_PLAIN = "Done: x\nTest: y\n"
LONG_LINE = "x" * 100  # 40 lines of this are about 4000 chars: over the Pattern cap, under the plain cap


class SliceCapTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        with open(os.path.join(self.root, "mod.py"), "w", encoding="utf-8") as f:
            f.write("\n".join(f"{n} {LONG_LINE}" for n in range(1, 60)) + "\n")

    def test_pattern_and_predicted_paths_use_the_small_cap(self):
        self.assertEqual(briefs.slice_chars(TASK_WITH_PATTERN), LEAD_SLICE_CHARS_WITH_PATTERN)

    def test_no_pattern_uses_the_large_cap(self):
        self.assertEqual(briefs.slice_chars(TASK_PLAIN), LEAD_SLICE_CHARS)
        self.assertGreater(LEAD_SLICE_CHARS, LEAD_SLICE_CHARS_WITH_PATTERN)

    def test_slices_are_cut_at_the_chosen_cap(self):
        leads = "mod.py:1-40"
        small = briefs.lead_slices(self.root, leads, max_chars=briefs.slice_chars(TASK_WITH_PATTERN))
        large = briefs.lead_slices(self.root, leads, max_chars=briefs.slice_chars(TASK_PLAIN))
        self.assertLessEqual(len(small), LEAD_SLICE_CHARS_WITH_PATTERN)
        self.assertGreater(len(large), LEAD_SLICE_CHARS_WITH_PATTERN)

    def test_mutation_always_large_cap_fails_the_pattern_case(self):
        # the always-6000 mutant: the Pattern task would get the large cap, so its slices would be over the small cap
        leads = "mod.py:1-40"
        mutant = briefs.lead_slices(self.root, leads, max_chars=LEAD_SLICE_CHARS)
        self.assertGreater(len(mutant), LEAD_SLICE_CHARS_WITH_PATTERN)
        self.assertNotEqual(briefs.slice_chars(TASK_WITH_PATTERN), LEAD_SLICE_CHARS)


def fake_local(stdout):
    return mock.patch.object(briefs, "run_py", return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=""))


class BuiltBriefTest(unittest.TestCase):
    """Tests on the brief strings the driver builds, not on the helpers alone."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        os.makedirs(os.path.join(self.d, WORK_DIR, "s"))
        with open(os.path.join(self.d, "mod.py"), "w", encoding="utf-8") as f:
            f.write("\n".join(f"{n} {LONG_LINE}" for n in range(1, 60)) + "\n")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(ENV_NO_LEADS, None)
        self.a = SimpleNamespace(dir=self.d, stage="s")

    def slices(self, key, text):
        with fake_local("mod.py:1-40\n"):
            out = briefs.leads_for(self.a, key, text)
        return out.split("Code of the leads (read it here, open the file only for more):\n", 1)[1]

    def test_leads_for_pattern_task_cuts_slices_at_3000(self):
        self.assertLessEqual(len(self.slices("p", TASK_WITH_PATTERN)), LEAD_SLICE_CHARS_WITH_PATTERN + 2)

    def test_leads_for_plain_task_keeps_the_large_cap(self):
        self.assertGreater(len(self.slices("q", TASK_PLAIN)), LEAD_SLICE_CHARS_WITH_PATTERN)

    def review(self, diff, gate_text):
        gate = os.path.join(self.d, "gate.txt")
        with open(gate, "w", encoding="utf-8") as f:
            f.write(gate_text)
        self.a.orch_events = [
            {"ev": "dispatch", "id": "s1", "task": "task-1", "role": "script", "why": "test run (x)"},
            {"ev": "report", "id": "s1", "task": "task-1", "role": "script", "verdict": "DONE", "file": gate},
        ]
        info = {"branch": "job/t", "base": "abc", "repos": {"r": "/w/r"}}
        with (
            mock.patch("pipeline.briefs.job_info", return_value=info),
            mock.patch("pipeline.briefs.job_diff", return_value=diff),
            mock.patch("pipeline.briefs.testrun_on", return_value=True),
            mock.patch("pipeline.briefs.qualityrun_on", return_value=False),
        ):
            return briefs._review_base(self.a, {"id": "task-1"})

    def test_reviewer_brief_inlines_the_green_gate_output_and_forbids_a_rerun(self):
        text = self.review("diff --git a/src/m.py b/src/m.py\n+y\n", "17 passed GATE-OUT-MARK\n")
        self.assertIn("GATE-OUT-MARK", text)
        self.assertIn("+y", text)
        self.assertIn("do not rerun them or the gate", text)
        self.assertNotIn("Docs-only", text)

    def test_docs_only_diff_names_the_turn_cap(self):
        text = self.review("diff --git a/docs/a.md b/docs/a.md\n+x\n", "ok\n")
        self.assertIn(f"at most {REVIEW_DOCS_MAX_TURNS} turns", text)

    def test_dev_brief_with_rework_file_carries_the_findings(self):
        rw = os.path.join(self.d, "rw.md")
        with open(rw, "w", encoding="utf-8") as f:
            f.write("- FINDING-MARK the guard is wrong\n")
        os.environ[ENV_NO_LEADS] = "1"
        with mock.patch("pipeline.briefs.job_info", return_value=None):
            prompt = briefs.prompt_for(self.a, {"id": "task-1"}, "dev", rw, "x1")
        self.assertIn("FINDING-MARK the guard is wrong", prompt)
        self.assertIn("Fix ALL of them in one pass", prompt)

    def test_lead_retry_note_passes_the_report_path_when_it_exists(self):
        rep = os.path.join(self.d, "report-a1.md")
        with open(rep, "w", encoding="utf-8") as f:
            f.write("REPORT-BODY-MARK\n")
        evs = [{"ev": "report", "id": "a1", "task": "task-1", "role": "dev", "verdict": "FAIL", "file": rep}]
        line = briefs.lead_note_line("redo it\nlong pasted findings", briefs.last_report_file(evs, "task-1"))
        self.assertIn(rep, line)
        self.assertIn("redo it", line)
        self.assertNotIn("long pasted findings", line)
        self.assertNotIn("REPORT-BODY-MARK", line)

    def test_lead_retry_note_without_a_report_keeps_the_note(self):
        evs = [{"ev": "report", "id": "a1", "task": "task-1", "role": "dev", "verdict": "FAIL", "file": os.path.join(self.d, "gone.md")}]
        task_id = evs[0]["task"]
        self.assertEqual(briefs.last_report_file(evs, task_id), "")
        self.assertEqual(briefs.lead_note_line("the full note\nline 2", ""), "Lead note (read first): the full note\nline 2")


class ReworkNoteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "rework.md")
        self.findings = "- reviewer: the guard misses the empty token\n- gate: ruff E501 at line 9\n"
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(self.findings)

    def test_findings_are_inlined_verbatim_with_fix_all_rule(self):
        note = briefs.rework_note(self.path)
        self.assertIn(self.findings, note)
        self.assertIn("Rework: fix the findings in " + self.path + " first. Fix ALL of them in one pass", note)

    def test_missing_file_names_the_path_only(self):
        gone = self.path + ".gone"
        note = briefs.rework_note(gone)
        self.assertIn(gone, note)
        self.assertIn("Fix ALL of them in one pass", note)
        self.assertNotIn("verbatim", note)

    def test_no_rework_file_is_empty(self):
        self.assertEqual(briefs.rework_note(""), "")
        self.assertEqual(briefs.rework_note(None), "")


class DocsOnlyTest(unittest.TestCase):
    def test_markdown_only_diff_is_docs_only(self):
        diff = "diff --git a/docs/a.md b/docs/a.md\n+x\ndiff --git a/README.md b/README.md\n+y\n"
        self.assertTrue(briefs.docs_only(diff))

    def test_code_in_the_diff_is_not_docs_only(self):
        diff = "diff --git a/docs/a.md b/docs/a.md\n+x\ndiff --git a/src/m.py b/src/m.py\n+y\n"
        self.assertFalse(briefs.docs_only(diff))

    def test_empty_or_unparsed_diff_is_not_docs_only(self):
        self.assertFalse(briefs.docs_only(""))
        self.assertFalse(briefs.docs_only(None))
        self.assertFalse(briefs.docs_only("no header here"))


class EscalationNoteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.a = SimpleNamespace(dir=tmp.name, feature="feat-x")
        fd = os.path.join(tmp.name, WORK_DIR, "feat-x")
        os.makedirs(fd)
        self.fd = fd
        with open(os.path.join(fd, "plan-last.md"), "w", encoding="utf-8") as f:
            f.write("ESCALATE: the draft misses the migration step")
        with open(os.path.join(fd, PLAN_FILE), "w", encoding="utf-8") as f:
            f.write("# Plan DRAFT-PLAN-MARK\n")
        with open(os.path.join(fd, TASKS_FILE), "w", encoding="utf-8") as f:
            f.write("## task-1 DRAFT-TASKS-MARK\n")

    def test_note_carries_reason_draft_and_amend_rule(self):
        note = plan_run.escalation_note(self.a)
        self.assertIn("ESCALATE: the draft misses the migration step", note)
        self.assertIn("DRAFT-PLAN-MARK", note)
        self.assertIn("DRAFT-TASKS-MARK", note)
        self.assertIn("Amend that draft", note)
        self.assertIn("do not replan from scratch", note)

    def test_draft_is_cut_at_the_cap(self):
        with open(os.path.join(self.fd, TASKS_FILE), "w", encoding="utf-8") as f:
            f.write("y" * (ESCALATE_DRAFT_CHARS * 2))
        note = plan_run.escalation_note(self.a)
        self.assertLess(note.count("y"), ESCALATE_DRAFT_CHARS * 2)


if __name__ == "__main__":
    unittest.main()
