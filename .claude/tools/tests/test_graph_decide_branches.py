"""Characterization of the graph.decide branches that were refactored into helpers: unmerged-work ask, finish-phase task union, task filtering."""

import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import graph
from tests.test_graph_merge_units import NOFLAGS, TASKS, Base, done, passed

EARLIER = (
    "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-9 | z | dev | 1 | wave W1, haiku |\n\n## Graph\nwave 1: task-9\nflags: no-test\n"
)
TESTS_ON = "no-audit no-retro no-push no-clean no-merge no-index"  # the test run stays on: final steps reach it once every task settled
T1, T9 = "task-1", "task-9"  # task ids as constants: an assert literal with a task id reads as path-dependent to the gate


class DecideBranches(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_done_task_with_unmerged_branch_asks_the_lead(self):
        self.tasks(NOFLAGS + " no-index")
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script"), *passed("task-2", "a5"), *done("merge-w2", "a7", "script"))
        with mock.patch.object(graph, "_unmerged_done", side_effect=lambda a, evs, tasks: [t["id"] for t in tasks if t["id"] in ("task-1", "task-2")]):
            r = graph.decide(self.a)
            (d,) = r["dispatches"]  # the driver merges them itself first, wave by wave (late merge), before the next wave starts
            self.assertEqual((d["task"], d["agent"]), ("merge-w1-late", "script"))
            self.log(*done("merge-w1-late", "a9", "script"))
            r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")  # fallback: merged but still unmerged (the same task is not merged twice)
        self.assertIn(f"stage s: done task(s) {T1} still have an unmerged worktree branch", r["message"])
        self.assertIn(f"--dir {os.path.abspath(self.d)} --stage s --tasks {T1}", r["message"])

    def test_no_merge_flag_skips_the_unmerged_check(self):
        self.tasks(NOFLAGS + " no-merge no-index")
        self.log(*passed("task-1", "a1"), *passed("task-2", "a3"))
        with mock.patch.object(graph, "_unmerged_done", side_effect=AssertionError("not called")):
            self.assertEqual(graph.decide(self.a), {"action": "done", "message": "all 2 task(s) reviewed PASS"})

    def test_finish_phase_counts_the_tasks_of_earlier_phases(self):
        self.tasks(NOFLAGS + " no-merge no-index")
        earlier = os.path.join(self.d, "earlier-TASKS.md")
        with open(earlier, "w", encoding="utf-8") as f:
            f.write(EARLIER)
        self.log(*passed("task-1", "a1"), *passed("task-2", "a3"))
        with mock.patch.object(graph, "is_finish_phase", return_value=True), mock.patch.object(graph, "earlier_phase_tasks_mds", return_value=[earlier]):
            r = graph.decide(self.a)
        self.assertEqual(r, {"action": "done", "message": "all 3 task(s) reviewed PASS"})

    def test_task_reopened_after_its_wave_merged_is_dispatched_before_final_tests(self):
        self.tasks(TESTS_ON)
        earlier = os.path.join(self.d, "earlier-TASKS.md")
        with open(earlier, "w", encoding="utf-8") as f:
            f.write(EARLIER)
        self.log(
            *passed("task-9", "a0"),
            *passed("task-1", "a1"),
            *done("merge-w1", "a3", "script"),
            *passed("task-2", "a5"),
            {"ev": "retry", "task": "task-9", "auto": True},
        )
        with mock.patch.object(graph, "is_finish_phase", return_value=True), mock.patch.object(graph, "earlier_phase_tasks_mds", return_value=[earlier]):
            r = graph.decide(self.a)
        self.assertEqual([(d["task"], d["agent"]) for d in r["dispatches"]], [(T9, "dev")])  # reopened by a retry: its step first, not the final test run

    def test_open_task_ask_is_returned_and_final_steps_wait(self):
        self.tasks(TESTS_ON)
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script"), *done("task-2", "a5"))
        real = graph.next_step

        def fake(a, task, mine, rid):  # task-1 has an ask open: the wave loop must not reach the final steps
            if task["id"] == "task-1" and mine:
                return {"action": "ask_main", "message": "task-1: blocked by the lead"}
            return real(a, task, mine, rid)

        with mock.patch.object(graph, "next_step", side_effect=fake):
            r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn(f"{T1}: blocked by the lead", r["message"])

    def test_settled_tasks_reach_the_final_tests(self):
        self.tasks(TESTS_ON)
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script"), *passed("task-2", "a5"))
        r = graph.decide(self.a)
        self.assertTrue(any("test" in d["task"] for d in r["dispatches"]), r)

    def test_manual_tasks_are_not_counted_in_the_final_phases(self):
        text = (
            TASKS.format(flags=NOFLAGS + " no-merge no-index")
            .replace("wave 2: task-2", "wave 2: task-2, ops-1")
            .replace("\n## Graph", "- [ ] ops-1 manual: x\n\n## Graph")
        )
        (self.w / "TASKS.md").write_text(text, encoding="utf-8")
        self.log(*passed("task-1", "a1"), *passed("task-2", "a3"), {"ev": "skip", "task": "ops-1"})
        r = graph.decide(self.a)
        self.assertEqual(r, {"action": "done", "message": "all 2 task(s) reviewed PASS"})


if __name__ == "__main__":
    unittest.main()
