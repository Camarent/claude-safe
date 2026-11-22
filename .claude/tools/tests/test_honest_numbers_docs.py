"""Docs of the honest-numbers stage: metrics fields, lead rules, deferred items. Reads the repo docs, no temp files."""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class HonestNumbersDocsTest(unittest.TestCase):
    def test_metrics_fields_paragraph(self):
        text = read(os.path.join(".claude", "practices", "orch-pipeline.md"))
        self.assertIn("**`metrics.jsonl` fields**", text)
        for word in ("seconds_api", "whole session", "reason:", "LEAD-COST"):
            self.assertIn(word, text)

    def test_lead_rules_one_keyword_each(self):
        text = read(os.path.join(".claude", "ORCHESTRATOR.md"))
        self.assertIn("## Lead session rules", text)
        for word in ("Monitor until-loop", "cleared or compacted", "Notify on finish", "separate session"):
            self.assertIn(word, text)

    def test_deferred_bullets_in_readme(self):
        text = read("README.md")
        self.assertIn("Deferred (decided, not done):", text)
        self.assertIn("lp nav", text)
        self.assertIn("local-model gate judge", text)


if __name__ == "__main__":
    unittest.main()
