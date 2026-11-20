"""`Depends:` lines in TASKS.md: parsing, validation, and the scheduling that lets independent tasks go on while one is blocked."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import graph
from pipeline.tasks import depends_problem, parse_tasks
from tests.support.tmpdirs import tracked_mkdtemp

HEAD = "| task | what | owner | price | review |\n|---|---|---|---|---|\n"


def md(rows, graph_lines, flags="no-merge no-test no-audit no-retro no-index no-push no-clean"):
    return HEAD + rows + "\n## Graph\n" + graph_lines + f"\nflags: {flags}\nPRE-APPROVED: master merge\n"


def parse(text):
    d = Path(tracked_mkdtemp())
    (d / "TASKS.md").write_text(text, encoding="utf-8")
    return parse_tasks(str(d / "TASKS.md"))


class Parse(unittest.TestCase):
    def test_depends_on_the_row_or_a_later_line(self):
        t = parse(
            md(
                "| task-1 | a | dev | 1 | wave W1, haiku |\n| task-2 | b Depends: task-1 | dev | 1 | wave W2, haiku |\n| task-3 | c | dev | 1 | wave W2, haiku |\nDepends: task-1, task-2\n",
                "wave 1: task-1\nwave 2: task-2\nwave 3: task-3",
            )
        )
        by = {x["id"]: x.get("depends", []) for x in t}
        self.assertEqual(by, {"task-1": [], "task-2": ["task-1"], "task-3": ["task-1", "task-2"]})
        self.assertIsNone(depends_problem(t))

    def test_bad_depends_are_named(self):
        for dep, word in (("task-1", "itself"), ("task-9", "not in TASKS.md")):
            t = parse(md(f"| task-1 | a Depends: {dep} | dev | 1 | wave W1, haiku |\n", "wave 1: task-1"))
            self.assertIn(word, depends_problem(t))

    def test_same_wave_is_a_problem_and_unlisted_comes_after(self):
        t = parse(md("| task-1 | a | dev | 1 | wave W1, haiku |\n| task-2 | b Depends: task-1 | dev | 1 | wave W1, haiku |\n", "wave 1: task-1, task-2"))
        self.assertIn("later wave", depends_problem(t))
        t = parse(md("| task-1 | a | dev | 1 | wave W1, haiku |\n| task-2 | b Depends: task-1 | dev | 1 | none |\n", "wave 1: task-1"))
        self.assertIsNone(depends_problem(t))


class Schedule(unittest.TestCase):
    def setUp(self):
        self.d = Path(tracked_mkdtemp())
        self.w = self.d / ".work" / "s"
        self.w.mkdir(parents=True)
        (self.d / "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False}), encoding="utf-8")
        self.a = SimpleNamespace(dir=str(self.d), stage="s")
        self.evs = []
        rows = "".join(
            f"| task-{i} | x{dep} | dev | 1 | wave W, haiku |\n" for i, dep in ((1, ""), (2, ""), (3, " Depends: task-1"), (4, ""), (5, " Depends: task-2"))
        )
        (self.w / "TASKS.md").write_text(md(rows, "wave 1: task-1, task-2\nwave 2: task-3, task-4, task-5"), encoding="utf-8")

    def log(self, *events):
        self.evs.extend(events)
        (self.w / "orch.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.evs), encoding="utf-8")

    def done(self, t, n):
        self.log(
            {"ev": "dispatch", "task": t, "id": f"a{n}", "agent": "dev", "role": "dev", "model": "haiku", "why": None},
            {"ev": "report", "task": t, "id": f"a{n}", "role": "dev", "verdict": "DONE", "file": "d.md"},
            {"ev": "dispatch", "task": t, "id": f"b{n}", "agent": "reviewer", "role": "reviewer", "model": "haiku", "why": None},
            {"ev": "report", "task": t, "id": f"b{n}", "role": "reviewer", "verdict": "PASS", "file": "r.md"},
        )

    def blocked(self, t, n):
        self.log(
            {"ev": "dispatch", "task": t, "id": f"a{n}", "agent": "dev", "role": "dev", "model": "haiku", "why": None},
            {"ev": "report", "task": t, "id": f"a{n}", "role": "dev", "verdict": "BLOCKED", "file": "d.md"},
        )

    def test_blocked_task_holds_only_its_dependents(self):
        self.blocked("task-1", 1)
        self.done("task-2", 2)
        with mock.patch.object(graph, "run_index", return_value=0):
            r = graph.decide(self.a)
            self.assertEqual(sorted(d["task"] for d in r["dispatches"]), ["task-4", "task-5"])  # task-3 waits on the blocked task-1
            self.done("task-4", 3)
            self.done("task-5", 4)
            r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")  # only now the lead hears about it
        self.assertIn("task-1", r["message"])

    def test_a_wave_with_every_task_held_dispatches_no_merger(self):
        (self.w / "TASKS.md").write_text(
            md(
                "| task-1 | a | dev | 1 | wave W1, haiku |\n| task-2 | b Depends: task-1 | dev | 1 | wave W2, haiku |\n",
                "wave 1: task-1\nwave 2: task-2",
                flags="no-test no-audit no-retro no-index no-push no-clean",
            ),
            encoding="utf-8",
        )
        self.blocked("task-1", 1)
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")  # not a merger for the empty wave 2
        self.assertIn("task-2", r["message"])

    def test_without_depends_a_blocked_task_stops_the_stage(self):
        rows = "".join(f"| task-{i} | x | dev | 1 | wave W, haiku |\n" for i in (1, 2, 3))
        (self.w / "TASKS.md").write_text(md(rows, "wave 1: task-1, task-2\nwave 2: task-3"), encoding="utf-8")
        self.blocked("task-1", 1)
        self.done("task-2", 2)
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")

    def test_manual_step_does_not_stop_the_dev_tasks_of_its_wave(self):
        rows = "| task-1 | a | dev | 1 | wave W1, haiku |\n- [ ] ops-1 manual: rotate the key\n"
        (self.w / "TASKS.md").write_text(md(rows, "wave 1: task-1, ops-1"), encoding="utf-8")
        r = graph.decide(self.a)
        self.assertEqual([d["task"] for d in r["dispatches"]], ["task-1"])
        self.done("task-1", 1)
        r = graph.decide(self.a)  # nothing else can run: now the lead hears about the manual step
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("ops-1", r["message"])

    def test_bad_depends_ask_the_lead(self):
        (self.w / "TASKS.md").write_text(md("| task-1 | a Depends: task-7 | dev | 1 | wave W1, haiku |\n", "wave 1: task-1"), encoding="utf-8")
        r = graph.decide(self.a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("task-7", r["message"])


class UnmergedDeps(unittest.TestCase):
    def test_dependent_is_held_while_its_dependency_work_is_not_merged(self):
        t = {"id": "task-16", "depends": ["task-14"]}
        with mock.patch.object(graph, "job_info", return_value={"root": "x"}), mock.patch.object(graph, "tasks_merged", return_value=False) as tm:
            self.assertTrue(graph._unmerged_deps(SimpleNamespace(), t))
        tm.assert_called_once_with(mock.ANY, ["task-14"])
        with mock.patch.object(graph, "job_info", return_value={"root": "x"}), mock.patch.object(graph, "tasks_merged", return_value=True):
            self.assertFalse(graph._unmerged_deps(SimpleNamespace(), t))
        with mock.patch.object(graph, "job_info", return_value=None):  # no job worktree left: nothing to wait for
            self.assertFalse(graph._unmerged_deps(SimpleNamespace(), t))
        self.assertFalse(graph._unmerged_deps(SimpleNamespace(), {"id": "task-1"}))


class DryStub(unittest.TestCase):
    def test_stub_plan_parses_roles_and_sequences(self):
        from pipeline import cli_wave

        a = SimpleNamespace(stub=["task-1:dev=blocked", "task-2:reviewer=FAIL,PASS", "task-3=DONE"])
        self.assertEqual(cli_wave._stub_plan(a), {("task-1", "dev"): ["BLOCKED"], ("task-2", "reviewer"): ["FAIL", "PASS"], ("task-3", "dev"): ["DONE"]})


class FlagResolved(unittest.TestCase):
    def test_resolved_lines_become_events(self):
        with tempfile.TemporaryDirectory() as d:
            rep, log = Path(d, "r.md"), Path(d, "log.jsonl")
            rep.write_text("VERDICT: DONE\nRESOLVED: a.py: dev: other side contained\nnoise\n")
            log.write_text(json.dumps({"ev": "report", "task": "merge-fix-w1", "file": str(rep)}) + "\n")
            with mock.patch.object(graph, "paths", return_value=(d, str(log))):
                graph._flag_resolved(SimpleNamespace(), 1)
            evs = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual([e["ev"] for e in evs], ["report", "resolved"])
            self.assertIn("a.py: dev", evs[1]["line"])


class FeatureOverview(unittest.TestCase):
    def test_counts_done_and_estimates(self):
        from pipeline import cli_wave

        rows = [
            ({"dir": "01-a", "tasks": 24}, True, [("wave 1", ["t1", "t2", "t3"])], {"t1": "done", "t2": "open"}),
            ({"dir": "02-b", "tasks": 16}, False, [], {}),
        ]
        out = cli_wave._feature_overview(rows)
        self.assertIn("1/3 planned tasks done, 2 left, plus ~16 in phases not planned yet", out)
        self.assertIn("p1 01-a: 1/3 tasks done, 2 left", out)
        self.assertIn("p2 02-b: not planned, ~16 tasks estimated", out)


if __name__ == "__main__":
    unittest.main()
