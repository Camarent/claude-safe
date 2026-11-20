"""Agent frontmatter models match the model tables in pipeline.configuration; sec-auditor.md states the turn caps of the constants."""

import os
import re
import sys
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import configuration as cfg  # noqa: E402  (path set above)

AGENTS = os.path.join(TOOLS, "..", "agents")

# documented split (practices/orch-pipeline.md, model ladder): dev and reviewer start on Haiku; architect and the auditors on Sonnet
DOCUMENTED = {"dev": "haiku", "reviewer": "haiku", "architect": "sonnet", "arch-auditor": "sonnet", "sec-auditor": "sonnet"}


def _model(name):
    with open(os.path.join(AGENTS, name + ".md"), encoding="utf-8") as fh:
        head = fh.read().split("\n---", 1)[0]
    m = re.search(r"^model:\s*(\S+)$", head, re.M)
    return m.group(1) if m else None


class AgentModelTables(unittest.TestCase):
    def test_start_model_table_matches_frontmatter(self):
        for name, model in cfg.AUDITOR_START_MODEL.items():
            self.assertEqual(_model(name), model, name)

    def test_escalation_is_opus_and_only_for_escalating_auditors(self):
        self.assertEqual(cfg.AUDITOR_ESCALATE_MODEL, cfg.MODEL_OPUS)
        for name in cfg.AUDITOR_ESCALATE:
            self.assertIn(name, cfg.AUDITOR_START_MODEL)
            self.assertNotEqual(_model(name), cfg.MODEL_OPUS, name)

    def test_documented_split_matches_frontmatter(self):
        for name, model in DOCUMENTED.items():
            self.assertEqual(_model(name), model, name)

    def test_sec_auditor_turn_caps_match_constants(self):
        with open(os.path.join(AGENTS, "sec-auditor.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f"`auditor_max_turns` turns (default {cfg.AUDITOR_MAX_TURNS})", text)
        self.assertIn(f"`auditor_escalate_max_turns`, default {cfg.AUDITOR_ESCALATE_MAX_TURNS}", text)


if __name__ == "__main__":
    unittest.main()
