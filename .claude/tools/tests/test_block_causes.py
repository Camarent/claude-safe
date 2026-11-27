"""retro/block_causes.py: the block counts of a feature from the report events of orch.jsonl, by fixed keyword rules."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.util import read_jsonl
from retro import block_causes

# fixture orch.jsonl: 2 cite, 1 gate, 1 ignored path, 1 tooling, 2 other blocks; a DONE report and a dispatch never count
FIXTURE = [
    {"ev": "dispatch", "task": "task-1"},
    {"ev": "report", "task": "task-1", "verdict": "BLOCKED", "reason": "cite check: path does not exist"},
    {"ev": "report", "task": "task-2", "verdict": "FAIL", "reason": "bad citation in the brief"},
    {"ev": "report", "task": "task-3", "verdict": "BLOCKED", "reason": "gate red after 2 tries"},
    {"ev": "report", "task": "task-4", "verdict": "FAIL", "reason": "ignored 1 finding(s) outside the dev remit: x.py"},
    {"ev": "report", "task": "task-5", "verdict": "BLOCKED", "reason": "pytest: command not found"},
    {"ev": "report", "task": "task-6", "verdict": "BLOCKED", "reason": "the design is unclear"},
    {"ev": "report", "task": "task-7", "verdict": "FAIL", "reason": "wrong return value"},
    {"ev": "report", "task": "task-8", "verdict": "DONE", "reason": "gate green"},
    {"ev": "report", "task": "task-9", "verdict": "BLOCKED", "reason": "cite and gate both named"},
]
EXPECTED = {"cite": 3, "gate": 1, "ignored path": 1, "tooling": 1, "other": 2}


class BlockCausesTest(unittest.TestCase):
    def fixture_events(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        path = os.path.join(t.name, "orch.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(e) + "\n" for e in FIXTURE)
        return read_jsonl(path)

    def test_fixture_counts_by_cause(self):
        self.assertEqual(block_causes.count(self.fixture_events()), EXPECTED)

    def test_unclassified_block_counts_as_other_and_totals_add_up(self):
        counts = block_causes.count(self.fixture_events())
        self.assertEqual(counts["other"], 2)
        blocks = [e for e in FIXTURE if e.get("verdict") in ("BLOCKED", "FAIL")]
        self.assertEqual(sum(counts.values()), len(blocks))

    def test_cite_wins_over_gate_when_both_keywords_match(self):  # mutation check: swapping the cite and gate rules makes this fail
        self.assertEqual(block_causes.cause_of("cite and gate both named"), "cite")
        self.assertEqual(block_causes.cause_of("gate red after 2 tries"), "gate")

    def test_keyword_inside_a_longer_word_is_not_a_cause(self):
        self.assertEqual(block_causes.cause_of("could not investigate the design"), "other")
        self.assertEqual(block_causes.cause_of("the aggregate result is wrong"), "other")
        self.assertEqual(block_causes.cause_of("recite the brief"), "other")

    def test_line_names_the_total_and_every_cause(self):
        self.assertEqual(
            block_causes.line(EXPECTED),
            "- block causes (8 BLOCKED/FAIL reports): cite 3, gate 1, ignored path 1, tooling 1, other 2",
        )


if __name__ == "__main__":
    unittest.main()
