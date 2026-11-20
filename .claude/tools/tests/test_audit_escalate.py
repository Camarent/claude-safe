"""sec-auditor starts on Sonnet; its ESCALATE reruns it once on Opus with the larger turn cap."""

import argparse
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases, driver
from pipeline.configuration import AUDIT_ESCALATE_SUFFIX, AUDITOR_ESCALATE_MAX_TURNS, project
from pipeline.verdicts import verdict

FLAGS = {"effort": None, "why": "", "ui": False}


def rep(task, v):
    return {"ev": "report", "task": task, "verdict": v}


class EscalateTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        self.a = argparse.Namespace(dir=self.d, stage="f", branch=None)

    def test_first_pass_models(self):
        self.assertEqual(audit_phases._audit_effort_model(FLAGS, "sec-auditor", "", False)[1], "sonnet")
        self.assertEqual(audit_phases._audit_effort_model(dict(FLAGS, effort="high"), "sec-auditor", "", False)[1], "opus")

    def test_verdict_escalate_only_for_escalating_auditors(self):
        self.assertEqual(verdict("sec-auditor", "VERDICT: ESCALATE\nUNKNOWN: x")[0], "ESCALATE")
        self.assertEqual(verdict("ux-reviewer", "VERDICT: ESCALATE")[0], "BLOCKED")

    def test_audit_verdict_prefers_the_opus_rerun(self):
        evs = [rep("audit-sec", "ESCALATE"), rep("audit-sec" + AUDIT_ESCALATE_SUFFIX, "FIXES")]
        self.assertEqual(audit_phases.audit_verdict(evs, "audit-sec"), "FIXES")
        self.assertEqual(audit_phases.audit_verdict(evs[:1], "audit-sec"), "ESCALATE")

    def test_rerun_step_is_opus_once(self):
        kinds = [("audit-sec", "sec-auditor")]
        args = (self.a, project(self.d), FLAGS, False, False)
        steps = audit_phases._escalated_steps(*args, [rep("audit-sec", "ESCALATE")], kinds)
        self.assertEqual([(s["id"], s["model"]) for s in steps], [("audit-sec-opus", "opus")])
        self.assertEqual(audit_phases._escalated_steps(*args, [rep("audit-sec", "CLEAN")], kinds), [])

    def test_turn_cap_of_the_rerun(self):
        c = argparse.Namespace(dir=self.d, log=os.path.join(self.d, "none.jsonl"))
        self.assertEqual(driver._auditor_turns(c, {"id": "r", "task": "audit-sec-opus"}), AUDITOR_ESCALATE_MAX_TURNS)
        self.assertEqual(driver._auditor_turns(c, {"id": "r", "task": "audit-sec"}), project(self.d)["auditor_max_turns"])
        with open(os.path.join(self.d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"auditor_escalate_max_turns": 7}, f)
        self.assertEqual(driver._auditor_turns(c, {"id": "r", "task": "audit-sec-opus"}), 7)


if __name__ == "__main__":
    unittest.main()
