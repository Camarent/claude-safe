"""Named plan-review settings and the plan-reviewer agent file: names, values and report format."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import configuration as cfg  # import after the sys.path insert above

AGENT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agents", "plan-reviewer.md")


class PlanReviewSettingsTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(cfg.PLAN_REVIEW_ROUNDS, 1)
        self.assertEqual(cfg.PLAN_REVIEW_SMALL_MAX_TASKS, 7)
        self.assertEqual(cfg.PLAN_EXTRA_TASKS_ALLOWED, 10)
        self.assertEqual(cfg.PLAN_REVIEW_MODEL_LIGHT, cfg.MODEL_HAIKU)
        self.assertEqual(cfg.PLAN_REVIEW_MODEL_HEAVY, cfg.MODEL_SONNET)
        self.assertEqual(cfg.PLAN_REVIEW_MODEL_LIGHT, "haiku")
        self.assertEqual(cfg.PLAN_REVIEW_MODEL_HEAVY, "sonnet")


class PlanReviewerAgentTest(unittest.TestCase):
    def setUp(self):
        with open(AGENT_FILE, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_front_matter(self):
        self.assertTrue(self.text.startswith("---\nname: plan-reviewer\n"))
        self.assertIn("\nmodel: haiku\n", self.text)
        self.assertIn("\ntools: Read, Grep, Glob, Bash, Write\n", self.text)

    def test_report_format(self):
        self.assertIn("VERDICT: DONE", self.text)
        self.assertIn("VERDICT: FAIL", self.text)
        self.assertIn("VERDICT: BLOCKED", self.text)
        self.assertIn("GAP: <text>", self.text)
        self.assertIn("GAP-TASKS: <n>", self.text)
