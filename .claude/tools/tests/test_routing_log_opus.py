"""Every Opus dispatch logs a `route:` why: the auditor ESCALATE rerun, the audit effort-high pass and a lead request for Opus."""

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases, requests, routing_log
from pipeline.configuration import CLEAN, ESCALATE, EV_DISPATCH, MODEL_OPUS, project
from tests.support.tmpdirs import tracked_mkdtemp

FLAGS = {"effort": None, "why": "", "ui": False}
HIGH = dict(FLAGS, effort="high", why="flag")
TABLE = "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n## Graph\nwave 1: task-1\n"


def rep(task, v):
    return {"ev": "report", "task": task, "verdict": v}


def dispatch_event(step):
    return {"ev": EV_DISPATCH, "model": step["model"], "why": step["why"]}


class AuditOpusRouteTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="synthetic-project-")
        self.a = SimpleNamespace(dir=self.d, stage="f", branch=None)

    def test_auditor_escalate_rerun_logs_the_route(self):
        kinds = [("audit-sec", "sec-auditor")]
        steps = audit_phases._escalated_steps(self.a, project(self.d), FLAGS, False, False, [rep("audit-sec", ESCALATE)], kinds)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["model"], MODEL_OPUS)
        self.assertTrue(steps[0]["why"].startswith("route: auditor ESCALATE -> opus"), steps[0]["why"])
        self.assertEqual(routing_log.unlogged_routes([dispatch_event(steps[0])]), [])

    def test_effort_high_first_pass_logs_the_route(self):
        step = audit_phases._audit_step(self.a, project(self.d), HIGH, False, False, "audit-sec", "sec-auditor", {CLEAN})
        self.assertEqual(step["model"], MODEL_OPUS)
        self.assertTrue(step["why"].startswith("route: audit effort high -> opus"), step["why"])
        self.assertEqual(routing_log.unlogged_routes([dispatch_event(step)]), [])

    def test_sonnet_first_pass_logs_no_route(self):
        step = audit_phases._audit_step(self.a, project(self.d), FLAGS, False, False, "audit-sec", "sec-auditor", {CLEAN})
        self.assertEqual(step["model"], "sonnet")
        self.assertFalse(step["why"].startswith("route:"), step["why"])


class LeadRequestOpusRouteTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="synthetic-project-")
        self.w = os.path.join(self.d, ".work", "s")
        os.makedirs(os.path.join(self.w, "requests"))
        with open(os.path.join(self.w, "TASKS.md"), "w", encoding="utf-8") as f:
            f.write(TABLE)
        self.log = os.path.join(self.w, "orch.jsonl")
        self.a = SimpleNamespace(dir=self.d, stage="s")
        env = mock.patch.dict(
            os.environ, {"ORCH_AGENTS_DIR": os.path.join(self.d, "no-agents"), "ORCH_GATED": "publisher", "ORCH_MAX_DISPATCH": "5", "ORCH_MAX_OPUS": "1"}
        )
        env.start()
        self.addCleanup(env.stop)
        with open(os.path.join(self.w, "brief.md"), "w", encoding="utf-8") as f:
            f.write("do it")

    def put(self, name, req):
        with open(os.path.join(self.w, "requests", name), "w", encoding="utf-8") as f:
            f.write(json.dumps(req))

    def take(self):
        r = requests.take_request(self.a, self.w, self.log)
        self.assertIsNotNone(r)
        return r or {}

    def test_opus_request_logs_the_lead_reason(self):
        self.put("r1.json", {"agent": "scout", "model": "opus", "brief": ".work/s/brief.md", "reason": "hard audit"})
        r = self.take()
        self.assertEqual(r["action"], "dispatch")
        self.assertTrue(r["why"].startswith("route: lead request hard audit; "), r["why"])
        self.assertEqual(routing_log.unlogged_routes([dispatch_event(r)]), [])

    def test_opus_request_without_reason_still_logs_a_route(self):
        self.put("r2.json", {"agent": "scout", "model": "opus", "brief": ".work/s/brief.md"})
        r = self.take()
        self.assertTrue(r["why"].startswith("route: lead request no reason given; "), r["why"])
        self.assertEqual(routing_log.unlogged_routes([dispatch_event(r)]), [])

    def test_opus_request_over_the_limit_is_still_refused(self):
        with mock.patch.dict(os.environ, {"ORCH_MAX_OPUS": "0"}):
            self.put("r3.json", {"agent": "scout", "model": "opus", "brief": ".work/s/brief.md", "reason": "x"})
            self.assertIsNone(requests.take_request(self.a, self.w, self.log))
        with open(self.log, encoding="utf-8") as f:
            self.assertIn('"opus budget used up"', f.read())
