"""The dev brief says predicted paths are a hint, not a limit: a red gate or the Test line may need a file outside them."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs

SCOPE_SENTENCE = "Predicted paths are a hint, not a limit"


class _Run:
    def __init__(self, stage, d):
        self.stage = stage
        self.dir = d


class DevScopeBriefTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dev-scope-brief-")
        self.addCleanup(tmp.cleanup)
        self.a = _Run("s", os.path.abspath(tmp.name))

    def _dev(self, rework_file):
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=None):
            return briefs.prompt_for(self.a, {"id": "task-1"}, "dev", rework_file, "x1")

    def test_dev_prompt_says_predicted_paths_are_a_hint(self):
        prompt = self._dev(None)
        self.assertIn(SCOPE_SENTENCE, prompt)
        self.assertIn("never revert a correct fix to fit the scope", prompt)
        self.assertIn("The driver records extra files by script.", prompt)

    def test_rework_dev_prompt_says_predicted_paths_are_a_hint(self):
        prompt = self._dev("rw.md")
        self.assertIn("Rework: fix the findings in rw.md first.", prompt)
        self.assertIn(SCOPE_SENTENCE, prompt)

    def test_reviewer_prompt_is_unchanged_by_the_dev_note(self):
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=None):
            rev = briefs.prompt_for(self.a, {"id": "task-1"}, "reviewer", None, "x2")
        self.assertNotIn(SCOPE_SENTENCE, rev)


if __name__ == "__main__":
    unittest.main()
