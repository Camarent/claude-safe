"""The dev brief states the Bash rules, that a tooling BLOCKED names its cause, and forbids cs-audit only for a task whose Goal or Done mentions CCN."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs

TASK_ID = "t1"
BASH_PHRASES = [
    "One Bash command per call",
    "`git -C <dir>` never `cd <dir>`",
    "No `;`, `&&`, `|` chains outside the qrun form",
    "Model set on every Agent call",
]
TOOLING_PHRASE = "A BLOCKED on a tooling failure names the cause"
CCN_PHRASE = "never run `cs-audit`"


class _Run:
    def __init__(self, stage, d):
        self.stage = stage
        self.dir = d


class BriefRulesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="brief-rules-")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.abspath(tmp.name)

    def _prompt(self, task_text, role="dev", rework_text=None):
        t = os.path.join(self.root, ".work", "s", TASK_ID + ".md")
        os.makedirs(os.path.dirname(t), exist_ok=True)
        with open(t, "w", encoding="utf-8") as f:
            f.write(task_text)
        rework = None
        if rework_text is not None:
            rework = os.path.join(self.root, "rw.md")
            with open(rework, "w", encoding="utf-8") as f:
                f.write(rework_text)
        wt = os.path.join(self.root, ".wt", "s", TASK_ID, "repo")
        info = {"branch": "job/s-" + TASK_ID, "repos": {"repo": wt}, "base": "dev"}
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=info):
            return briefs.prompt_for(_Run("s", self.root), {"id": TASK_ID}, role, rework, "x1")

    def _dev_prompt(self, task_text, rework_text=None):
        return self._prompt(task_text, "dev", rework_text)

    def test_dev_brief_states_every_bash_rule(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: rename foo\nTest: true\n")
        for phrase in BASH_PHRASES:
            self.assertIn(phrase, prompt)

    def test_dev_brief_says_tooling_block_names_cause(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: rename foo\nTest: true\n")
        self.assertIn(TOOLING_PHRASE, prompt)

    def test_ccn_goal_brief_forbids_cs_audit(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: lower the CCN of parse\nTest: true\n")
        self.assertIn(CCN_PHRASE, prompt)

    def test_ccn_done_brief_forbids_cs_audit(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: tidy parse\nDone: C1: CCN of parse is under 10\nTest: true\n")
        self.assertIn(CCN_PHRASE, prompt)

    def test_done_naming_tool_output_brief_names_cs_devtools(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: tidy parse\nDone: C1: vulture reports nothing on parse.py\nTest: true\n")
        self.assertIn("cs-devtools <tool> <paths>", prompt)
        self.assertIn(CCN_PHRASE, prompt)

    def test_non_tool_output_done_brief_has_no_cs_devtools_line(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: rename foo\nDone: C1: foo is bar\nTest: true\n")
        self.assertNotIn("cs-devtools", prompt)

    def test_non_ccn_task_brief_has_no_cs_audit_line(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: rename foo\nDone: C1: foo is bar\nTest: true\n")
        self.assertNotIn("cs-audit", prompt)

    def test_reviewer_brief_ignores_index_diff(self):
        prompt = self._prompt("Predicted paths: x.py\nGoal: rename foo\nTest: true\n", role="reviewer")
        self.assertIn(briefs.REVIEW_INDEX_NOTE.strip(), prompt)

    def test_dev_brief_forbids_citing_line_numbers(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: rename foo\nTest: true\n")
        self.assertIn(briefs.DEV_CITE_NOTE.strip(), prompt)

    def test_lizard_task_brief_has_lizard_note(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: lower lizard CCN of parse\nTest: true\n")
        self.assertIn(briefs.LIZARD_NOTE.strip(), prompt)

    def test_audit_md_task_brief_has_lizard_note(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: tidy\nDone: C1: AUDIT.md row is green\nTest: true\n")
        self.assertIn(briefs.LIZARD_NOTE.strip(), prompt)

    def test_rework_mentioning_lizard_brief_has_lizard_note(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: tidy\nTest: true\n", rework_text="- lizard gate red on parse\n")
        self.assertIn(briefs.LIZARD_NOTE.strip(), prompt)

    def test_non_lizard_task_brief_has_no_lizard_note(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: rename foo\nTest: true\n")
        self.assertNotIn(briefs.LIZARD_NOTE.strip(), prompt)

    def test_ci_drift_task_brief_has_ci_drift_note(self):
        prompt = self._dev_prompt("Predicted paths: pipeline.json\nGoal: fix ci_drift_ignore entry\nTest: true\n")
        self.assertIn(briefs.CI_DRIFT_NOTE.strip(), prompt)

    def test_non_ci_drift_task_brief_has_no_ci_drift_note(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: rename foo\nTest: true\n")
        self.assertNotIn(briefs.CI_DRIFT_NOTE.strip(), prompt)

    def test_public_words_task_brief_has_public_words_note(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: clear public-words hits\nTest: true\n")
        self.assertIn(briefs.PUBLIC_WORDS_NOTE.strip(), prompt)

    def test_non_public_words_task_brief_has_no_public_words_note(self):
        prompt = self._dev_prompt("Predicted paths: x.py\nGoal: rename foo\nTest: true\n")
        self.assertNotIn(briefs.PUBLIC_WORDS_NOTE.strip(), prompt)


if __name__ == "__main__":
    unittest.main()
