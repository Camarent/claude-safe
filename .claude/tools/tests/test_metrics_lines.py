"""pipeline/metrics.py: session, event, wave and total lines in metrics.jsonl, and the per-model aggregate (synthetic data)."""

import argparse
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import metrics
from pipeline.claude import SUMS
from pipeline.ctx import Ctx
from pipeline.util import read_jsonl
from tests.support.tmpdirs import tracked_mkdtemp


def usage(cost=0.5, **extra):
    u = dict.fromkeys(SUMS, 1) | {"cost_usd": cost, "error": False, "sid": "sess-1"}
    return u | extra


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        os.makedirs(os.path.join(self.d, ".work", "s"))
        self.c = Ctx(argparse.Namespace(stage="s", dir=self.d))

    def lines(self):
        return read_jsonl(self.c.metrics)

    def test_session_line_keeps_sid_verdict_and_adds_cost(self):
        rec = metrics.session_line(self.c, "a1", "dev", "haiku", usage(0.25), verdict="DONE", task="task-1")
        self.assertEqual(
            (rec["ev"], rec["agent"], rec["model"], rec["sid"], rec["verdict"], rec["task"]), ("session", "dev", "haiku", "sess-1", "DONE", "task-1")
        )
        self.assertEqual(self.c.cost, 0.25)
        self.assertEqual(self.lines(), [rec])

    def test_reason_is_stored_only_for_blocked_and_fail(self):
        blocked = metrics.session_line(self.c, "a1", "dev", "haiku", usage(), verdict="BLOCKED", reason="tests/x.js missing")
        fail = metrics.session_line(self.c, "a2", "reviewer", "haiku", usage(), verdict="FAIL", reason="no test")
        done = metrics.session_line(self.c, "a3", "dev", "haiku", usage(), verdict="DONE", reason="all green")
        empty = metrics.session_line(self.c, "a4", "dev", "haiku", usage(), verdict="BLOCKED")
        self.assertEqual((blocked["reason"], fail["reason"]), ("tests/x.js missing", "no test"))
        self.assertNotIn("reason", done)
        self.assertNotIn("reason", empty)

    def test_no_model_records_the_agent_default_and_scripts_stay_null(self):
        from unittest import mock

        with mock.patch("pipeline.requests.agent_types", return_value={"sec-auditor": {"model": "opus"}}):
            self.assertEqual(metrics.session_line(self.c, "a1", "sec-auditor", None, usage())["model"], "opus")
            self.assertEqual(metrics.session_line(self.c, "a2", "sec-auditor", "sonnet", usage())["model"], "sonnet")
            self.assertIsNone(metrics.session_line(self.c, "a3", "script", None, usage())["model"])

    def test_tools_and_search_are_stored_only_when_present(self):
        plain = metrics.session_line(self.c, "a1", "dev", "haiku", usage())
        rich = metrics.session_line(self.c, "a2", "dev", "haiku", usage(tools={"Grep": 3}, search={"grep": 3}))
        self.assertNotIn("tools", plain)
        self.assertNotIn("search", plain)
        self.assertEqual((rich["tools"], rich["search"]), ({"Grep": 3}, {"grep": 3}))

    def test_event_line_is_free_form_and_not_a_session(self):
        metrics.event(self.c, "context", files=7, skipped=False)
        (line,) = self.lines()
        self.assertEqual((line["ev"], line["stage"], line["files"], line["skipped"]), ("context", "s", 7, False))
        self.assertEqual(metrics.aggregate(self.lines()), {})

    def test_wave_line_sums_the_wave_sessions_then_clears_them(self):
        self.c.wave = 2
        metrics.session_line(self.c, "a1", "dev", "haiku", usage(0.5))
        metrics.session_line(self.c, "a2", "reviewer", "sonnet", usage(1.5))
        metrics.wave_line(self.c, 12.34)
        wave = [r for r in self.lines() if r["ev"] == "wave"][0]
        self.assertEqual((wave["wave"], wave["sessions"], wave["cost_usd"], wave["seconds_wall"]), (2, 2, 2.0, 12.3))
        self.assertEqual(self.c.sess, [])

    def test_total_line_counts_every_session(self):
        metrics.session_line(self.c, "a1", "dev", "haiku", usage(0.5))
        metrics.session_line(self.c, "a2", "dev", "haiku", usage(0.5))
        self.c.wave = 1
        metrics.total_line(self.c)
        total = self.lines()[-1]
        self.assertEqual((total["ev"], total["sessions"], total["waves"]), ("total", 2, 1))

    def test_aggregate_groups_by_model_and_agent(self):
        metrics.session_line(self.c, "a1", "dev", "haiku", usage(0.5))
        metrics.session_line(self.c, "a2", "dev", "haiku", usage(0.25))
        metrics.session_line(self.c, "a3", "dev", "sonnet", usage(1.0))
        agg = metrics.aggregate(self.lines())
        self.assertEqual(set(agg), {("haiku", "dev"), ("sonnet", "dev")})
        self.assertEqual((agg[("haiku", "dev")]["sessions"], agg[("haiku", "dev")]["cost_usd"]), (2, 0.75))


if __name__ == "__main__":
    unittest.main()
