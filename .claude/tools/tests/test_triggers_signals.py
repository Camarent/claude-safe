"""Characterization tests for triggers.retro_signals: one case per signal."""

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import triggers
from tests.support.tmpdirs import tracked_mkdtemp


def table(n):
    rows = "".join(f"| task-{i} | a | dev | 1 | wave 1, haiku |\n" for i in range(1, n + 1))
    return (
        "| task | what | owner | price | review |\n|---|---|---|---|---|\n"
        + rows
        + "\n## Graph\nwave 1: "
        + ", ".join(f"task-{i}" for i in range(1, n + 1))
        + "\n"
    )


def rep(task, verdict, role="dev"):
    return {"ev": "report", "task": task, "role": role, "verdict": verdict}


class RetroSignalsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="synthetic-project-")
        self.w = os.path.join(self.d, ".work", "s")
        os.makedirs(self.w)
        self.a = SimpleNamespace(dir=self.d, stage="s")
        env = mock.patch.dict(os.environ, {"ORCH_MAX_DISPATCH": "20"})
        env.start()
        self.addCleanup(env.stop)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def go(self, tasks=4, evs=(), trig=()):
        self.write(os.path.join(self.w, "TASKS.md"), table(tasks))
        self.write(os.path.join(self.w, "orch.jsonl"), "".join(json.dumps(e) + "\n" for e in evs))
        self.write(os.path.join(self.d, ".work", "triggers.jsonl"), "".join(json.dumps(e) + "\n" for e in trig))
        return triggers.retro_signals(self.a)

    def test_quiet_stage(self):
        self.assertEqual(self.go(), [])

    def test_high_effort(self):
        trig = [
            {"ev": "trigger", "stage": "s", "effort": "high", "id": "a1", "agent": "sec-auditor"},
            {"ev": "trigger", "stage": "other", "effort": "high", "id": "a2", "agent": "x"},
        ]
        self.assertEqual(self.go(trig=trig), [("high-effort", "1 call(s) at effort high: a1 sec-auditor")])

    def test_review_rounds(self):
        evs = [rep("task-1", "FAIL", "reviewer"), rep("task-1", "FAIL", "reviewer"), rep("task-2", "FAIL", "reviewer")]
        self.assertEqual(self.go(evs=evs), [("review-rounds", "tasks with 2+ failed review rounds: task-1")])

    def test_escalations(self):
        evs = [rep("task-1", "ESCALATE"), rep("task-2", "ESCALATE")]
        self.assertEqual(self.go(evs=evs), [("escalations", "2 of 4 tasks escalated to Sonnet (>25%)")])

    def test_useless_escalation(self):
        def disp(i, task, model):
            return {"ev": "dispatch", "id": i, "task": task, "role": "dev", "model": model}

        def dev(i, task, verdict):
            return {"ev": "report", "id": i, "task": task, "role": "dev", "verdict": verdict}

        evs = [disp("a1", "task-1", "haiku"), dev("a1", "task-1", "ESCALATE"), disp("a2", "task-1", "sonnet"), dev("a2", "task-1", "BLOCKED")]
        evs += [disp("a3", "task-2", "haiku"), dev("a3", "task-2", "ESCALATE"), disp("a4", "task-2", "sonnet"), dev("a4", "task-2", "DONE")]
        got = [x for x in self.go(evs=evs) if x[0] == "useless-escalations"]
        self.assertEqual(got, [("useless-escalations", "Haiku ESCALATE then Sonnet not DONE, investigate: task-1: haiku ESCALATE > sonnet BLOCKED")])

    def test_audit_fixes(self):
        evs = [rep("audit-sec", "FIXES"), rep("audit-arch", "CLEAN"), rep("audit-ux", "FIXES")]
        self.assertEqual(self.go(evs=evs), [("audit-fixes", "sec/arch audits asked for fixes: audit-sec")])

    def test_fix_share(self):
        evs = [rep("fix-plan-1", "DONE"), {"ev": "dispatch", "task": "task-4", "role": "dev"}, {"ev": "dispatch", "task": "task-3", "role": "dev"}]
        self.assertEqual(self.go(evs=evs), [("fix-share", "2 fix task(s) vs 2 base task(s) (>20%)")])

    def test_stuck_by_asks_and_by_manual_retry(self):
        self.assertEqual(self.go(evs=[{"ev": "ask"}] * 3), [("stuck", "3 ask_main stop(s), 0 retry/skip")])
        evs = [{"ev": "retry", "task": "task-1"}, {"ev": "skip", "task": "master-merged"}, {"ev": "skip", "task": "task-2", "auto": True}]
        self.assertEqual(self.go(evs=evs), [("stuck", "0 ask_main stop(s), 1 retry/skip")])

    def test_dispatches_over_three_quarters_of_the_limit(self):
        evs = [{"ev": "dispatch", "agent": "dev", "model": "sonnet", "task": f"x{i}"} for i in range(4)]
        with mock.patch.dict(os.environ, {"ORCH_MAX_DISPATCH": "5"}):
            self.assertEqual(self.go(evs=evs), [("dispatches", "4 Sonnet/Opus dispatches (limit 5)")])

    def test_retro_numbers_lines_are_passed_through(self):
        self.write(os.path.join(self.w, "retro", "retro-numbers.md"), "text\nRETRO context: agent a hit 150k\nRETRO cost-x: 12 usd\n")
        self.assertEqual(self.go(), [("context", "agent a hit 150k"), ("cost-x", "12 usd")])


if __name__ == "__main__":
    unittest.main()
