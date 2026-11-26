"""A wave with a manual step still shows the asks of its other tasks and holds tasks that depend on open ones."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import graph


class ManualWave(unittest.TestCase):
    def setUp(self):
        self.a = SimpleNamespace(dir=".", stage="s")
        self.tasks = [
            {"id": "t1", "wave": 1},
            {"id": "t2", "wave": 1, "depends": ["t0"]},
            {"id": "ops", "wave": 1, "manual": "rotate the key"},
        ]

    def test_other_asks_are_shown_with_the_manual_step(self):
        with mock.patch.object(graph, "_wave_steps", return_value=[{"action": "ask_main", "message": "t1 BLOCKED: boom"}]):
            r = graph._run_wave(self.a, [], self.tasks, {}, 1, 0)
        self.assertIn("t1 BLOCKED: boom", r.action["message"])
        self.assertIn("manual step ops", r.action["message"])
        self.assertEqual((r.opened, r.held, r.asks), (set(), set(), []))  # no dependency mode: nothing collected

    def test_manual_step_with_open_dependency_holds_and_dispatches_the_rest(self):
        disp = {"action": "dispatch", "dispatches": [{"task": "t1"}]}
        with mock.patch.object(graph, "_wave_steps", return_value=[disp]) as ws, mock.patch.object(graph, "_unmerged_deps", return_value=False):
            r = graph._run_wave(self.a, [], self.tasks, {}, 1, 0, {"t0"})
        self.assertEqual(r.action, {"action": "dispatch", "dispatches": [disp]})
        self.assertEqual([t["id"] for t in ws.call_args[0][2]], ["t1"])
        self.assertEqual((r.opened, r.held), ({"ops", "t2"}, {"t2"}))

    def test_dependents_of_open_tasks_are_held(self):
        with mock.patch.object(graph, "_wave_steps", return_value=[]) as ws, mock.patch.object(graph, "_unmerged_deps", return_value=False):
            r = graph._run_wave(self.a, [], self.tasks, {}, 1, 0, {"t0"})
        self.assertEqual([t["id"] for t in ws.call_args[0][2]], ["t1"])
        self.assertIn("t2", r.held)

    def test_dependency_mode_collects_asks_and_partially_merges(self):
        ask = [{"action": "ask_main", "message": "t1 BLOCKED"}]
        tasks = [{"id": "t1", "wave": 1}]
        with (
            mock.patch.object(graph, "_wave_steps", return_value=ask),
            mock.patch.object(graph, "next_step", return_value="dev"),
            mock.patch.object(graph, "_partial_merge", return_value=None) as pm,
        ):
            r = graph._run_wave(self.a, [], tasks, {"merge": True}, 1, 0, set())
        self.assertEqual((r.asks, r.opened, r.action), (ask, {"t1"}, None))
        self.assertEqual(pm.call_args[0][2], [])

    def test_old_rule_asks_all(self):
        ask = [{"action": "ask_main", "message": "t1 BLOCKED"}]
        with mock.patch.object(graph, "_wave_steps", return_value=ask):
            r = graph._run_wave(self.a, [], [{"id": "t1", "wave": 1}], {}, 1, 0)
        self.assertIn("t1 BLOCKED", r.action["message"])


if __name__ == "__main__":
    unittest.main()
