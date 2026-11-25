"""The escalation rules E1-E4 in the four docs use the owner's words and the driver's constants (verdicts, HAIKU_REWORK_TRIES)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.configuration import HAIKU_REWORK_TRIES
from pipeline.configuration.verdicts import ESCALATE, FAIL, PASS

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DOCS = [
    os.path.join(".claude", "ORCHESTRATOR.md"),
    os.path.join(".claude", "practices", "orch-pipeline.md"),
    os.path.join(".claude", "agents", "dev.md"),
    os.path.join(".claude", "agents", "reviewer.md"),
]
WORDS = {1: "one", 2: "two", 3: "three"}  # the count of Haiku tries, spelled as the docs write it
TRIES = WORDS[1 + HAIKU_REWORK_TRIES]  # one Haiku try, then the rework try: two Haiku tries in all


def _verdicts():
    return ", ".join(f"`{v}`" for v in (PASS, FAIL, ESCALATE))


PHRASES = {
    "E1": (
        "E1: a dev or reviewer step moves from Haiku to Sonnet only after one of these three events, and no other: "
        f"(a) the gate command stayed red after {TRIES} Haiku tries; (b) the dev reply is `{ESCALATE}:` and names one concrete reason; "
        f"(c) the reviewer verdict is `{ESCALATE}` and names one defect with file and line."
    ),
    "E2": (
        f"E2: a reviewer verdict is exactly one of {_verdicts()}. `{PASS}` means wrong, risky and nit counts are all 0. "
        f"`{ESCALATE}` with all three counts 0 is invalid: the driver treats it as `{PASS}` and logs a warning with the report id."
    ),
    "E3": 'E3: a file path pattern never selects a model. Do not add routing by path, topic or word such as "network" or "parser".',
    "E4": (
        "E4: an escalation runs once per task and step. After it the next verdict is final for that round; "
        f"a second `{ESCALATE}` of the same kind blocks to the lead with the named reason."
    ),
}


AGENT_DOCS = [
    os.path.join(".claude", "agents", "dev.md"),
    os.path.join(".claude", "agents", "reviewer.md"),
]
DENIAL_PHRASES = (
    "cwd is the job worktree; run plain `git <args>`, never `cd` before git.",
    "`cs-qrun -- <cmd>` alone in one Bash call: no pipe, no `;`, no `cd`.",
    "Tests take env only through the documented flag, never a `VAR=x` prefix.",
    "Read a range with the Read tool (offset/limit); `sed -n 'A,Bp' file` is one range per call.",
    "No shell variable assignment before a command; write absolute paths.",
)


def missing_phrases(text):
    """-> the rule ids (E1-E4) whose phrase is not in TEXT word for word."""
    return [rule for rule, phrase in PHRASES.items() if phrase not in text]


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


class DenialFormsDocsTest(unittest.TestCase):
    def test_each_agent_doc_states_the_five_denial_rules(self):
        for rel in AGENT_DOCS:
            text = _read(rel)
            for phrase in DENIAL_PHRASES:
                with self.subTest(doc=rel, phrase=phrase):
                    self.assertIn(phrase, text)

    def test_reviewer_doc_has_no_review_ccn_route(self):
        self.assertNotIn("review_ccn", _read(os.path.join(".claude", "agents", "reviewer.md")))


class EscalationDocsTest(unittest.TestCase):
    def test_each_doc_states_e1_to_e4_word_for_word(self):
        for rel in DOCS:
            with self.subTest(doc=rel):
                self.assertEqual(missing_phrases(_read(rel)), [])

    def test_constants_match_the_phrases(self):
        self.assertEqual(TRIES, "two")  # E1(a) names two Haiku tries: HAIKU_REWORK_TRIES 1 plus the first try
        self.assertEqual((PASS, FAIL, ESCALATE), ("PASS", "FAIL", "ESCALATE"))  # E2 names the verdicts of configuration.verdicts

    def test_mutated_copy_is_reported(self):
        # an in-memory copy with one phrase altered: the checker names that rule and no other
        text = _read(DOCS[0])
        self.assertEqual(missing_phrases(text), [])
        mutated = text.replace(PHRASES["E1"], PHRASES["E1"].replace("two Haiku tries", "three Haiku tries"))
        self.assertNotEqual(mutated, text)
        self.assertEqual(missing_phrases(mutated), ["E1"])


if __name__ == "__main__":
    unittest.main()
