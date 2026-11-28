"""The research brief tells the scout to cite upstream files as `file.go line 87`, never `file.go:87`."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs

CITE_SENTENCE = "Cite upstream files (outside the repo) as `file.go line 87`: the word line goes between the name and the number, never a colon (`file.go:87`)"


class _Run:
    def __init__(self, d):
        self.stage = "s"
        self.dir = d


class ResearchCiteRuleTest(unittest.TestCase):
    def test_research_brief_carries_line_form_cite_rule(self):
        # Removing CITE_SENTENCE from research_prompt makes this assertion fail (mutation check).
        prompt = briefs.research_prompt(_Run(os.path.dirname(os.path.abspath(__file__))), {"id": "task-1"}, "a1")
        self.assertIn(CITE_SENTENCE, prompt)


if __name__ == "__main__":
    unittest.main()
