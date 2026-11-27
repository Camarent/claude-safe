"""pipeline.routing_log: unlogged Sonnet/Opus routes and off-rule E1(a)/E1(b) dispatches over orch.jsonl events."""

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import routing_log
from pipeline.configuration import ESCALATE, EV_DISPATCH, EV_REPORT, FAIL, HAIKU_DEV_TRIES, MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET

TASK = "task-5"


def dispatch(sid, role, model=MODEL_HAIKU, why=None, task=TASK):
    """Synthetic orch.jsonl dispatch event of step SID for TASK."""
    e = {"ev": EV_DISPATCH, "id": sid, "task": task, "role": role, "model": model}
    if why is not None:
        e["why"] = why
    return e


def report(sid, role, verdict, task=TASK):
    """Synthetic orch.jsonl report event of step SID for TASK."""
    return {"ev": EV_REPORT, "id": sid, "task": task, "role": role, "verdict": verdict}


def haiku_devs(n, task=TASK, start=0):
    """N Haiku dev dispatches of TASK with no E1 why (plain Haiku tries)."""
    return [dispatch(f"h{start + i}", "dev", task=task) for i in range(n)]


class UnloggedRoutesTest(unittest.TestCase):
    def test_a36_a37_shape_extra_review_round_without_letter_is_unlogged(self):
        # a36/a37: a reviewer round on Sonnet whose why is the plain review text, no E1 letter, no route: prefix
        events = [dispatch("a36", "reviewer", model=MODEL_SONNET, why="extra review round"), dispatch("a37", "dev", model=MODEL_SONNET)]
        self.assertEqual([e["id"] for e in routing_log.unlogged_routes(events)], ["a36", "a37"])

    def test_e1_letter_logs_the_route(self):
        events = [dispatch("d1", "dev", model=MODEL_SONNET, why="E1(b): dev escalated; Sonnet dev")]
        self.assertEqual(routing_log.unlogged_routes(events), [])

    def test_route_prefix_logs_an_opus_route(self):
        events = [dispatch("o1", "reviewer", model=MODEL_OPUS, why="route: auditor rerun on Opus")]
        self.assertEqual(routing_log.unlogged_routes(events), [])

    def test_opus_without_why_is_unlogged(self):
        events = [dispatch("o2", "reviewer", model=MODEL_OPUS)]
        self.assertEqual([e["id"] for e in routing_log.unlogged_routes(events)], ["o2"])

    def test_haiku_dispatch_is_never_unlogged(self):
        self.assertEqual(routing_log.unlogged_routes(haiku_devs(3)), [])

    def test_report_events_are_ignored(self):
        self.assertEqual(routing_log.unlogged_routes([report("r1", "reviewer", FAIL)]), [])


class OffRuleTest(unittest.TestCase):
    def test_e1a_with_too_few_haiku_tries_is_off_rule(self):
        events = [*haiku_devs(HAIKU_DEV_TRIES - 1), dispatch("x1", "dev", model=MODEL_SONNET, why="E1(a): dev gate stayed red; Sonnet dev")]
        self.assertEqual([e["id"] for e in routing_log.off_rule(events)], ["x1"])

    def test_e1a_at_haiku_dev_tries_is_on_rule(self):
        # boundary: exactly HAIKU_DEV_TRIES Haiku tries is on-rule; a flipped comparison (<=) flags this fixture
        events = [*haiku_devs(HAIKU_DEV_TRIES), dispatch("x2", "dev", model=MODEL_SONNET, why="E1(a): dev gate stayed red; Sonnet dev")]
        self.assertEqual(routing_log.off_rule(events), [])

    def test_e1a_haiku_tries_of_another_task_do_not_count(self):
        decoy = haiku_devs(HAIKU_DEV_TRIES, task="task-9")
        events = [*decoy, dispatch("x3", "dev", model=MODEL_SONNET, why="E1(a): dev gate stayed red; Sonnet dev")]
        self.assertEqual([e["id"] for e in routing_log.off_rule(events)], ["x3"])

    def test_e1b_with_one_escalate_is_off_rule(self):
        events = [report("e1", "dev", ESCALATE), dispatch("x4", "dev", model=MODEL_SONNET, why="E1(b): dev escalated; Sonnet dev")]
        self.assertEqual([e["id"] for e in routing_log.off_rule(events)], ["x4"])

    def test_e1b_with_two_escalates_is_on_rule(self):
        events = [
            report("e1", "dev", ESCALATE),
            report("e2", "dev", ESCALATE),
            dispatch("x5", "dev", model=MODEL_SONNET, why="E1(b): dev escalated; Sonnet dev"),
        ]
        self.assertEqual(routing_log.off_rule(events), [])

    def test_e1b_escalates_of_another_task_do_not_count(self):
        events = [
            report("e1", "dev", ESCALATE, task="task-9"),
            report("e2", "dev", ESCALATE, task="task-9"),
            dispatch("x6", "dev", model=MODEL_SONNET, why="E1(b): dev escalated; Sonnet dev"),
        ]
        self.assertEqual([e["id"] for e in routing_log.off_rule(events)], ["x6"])

    def test_non_dev_dispatch_is_not_checked(self):
        events = [dispatch("x7", "reviewer", model=MODEL_SONNET, why="E1(a): dev gate stayed red; Sonnet dev")]
        self.assertEqual(routing_log.off_rule(events), [])


class ImportRuleTest(unittest.TestCase):
    def test_module_imports_nothing_from_steps(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline", "routing_log.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertFalse([m for m in modules if "steps" in m.split(".")])


if __name__ == "__main__":
    unittest.main()
