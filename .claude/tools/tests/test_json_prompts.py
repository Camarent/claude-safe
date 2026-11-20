"""The model prompts keep their exact JSON-answer wording behind localmodel.prompts."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import askmodel, escalation_gate, prompts
from search import local

TAIL = 'Answer with ONE json object only: {"verdict":"PASS|FAIL","reason":"<20 words>","evidence":"<exact line or empty>"}'
REVIEW_TAIL = prompts.REVIEW_FINDINGS_ANSWER


class JsonPromptTest(unittest.TestCase):
    def test_gate_prompt(self):
        self.assertTrue(escalation_gate.build_prompt("Goal: x", "f").endswith("Be strict. " + TAIL))

    def test_review_rules(self):
        self.assertTrue(local.RULES.endswith(" " + REVIEW_TAIL))

    def test_translate_prompt(self):
        with mock.patch.object(askmodel, "llm", return_value="{}") as m:
            askmodel.translate("how?")
        self.assertTrue(m.call_args[0][0].endswith('Question: how?\nAnswer with ONE json object only: {"symbols":["..."],"keywords":["..."]}'))


if __name__ == "__main__":
    unittest.main()
