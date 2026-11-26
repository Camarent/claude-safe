"""`plan.py task --before N`: renumbering, started tasks, cycles, overlap, and what wave.py (graph.decide) and the counters see afterwards."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan, graph, progress
from pipeline.tasks import parse_tasks
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

FLAGS = "no-merge no-test no-audit no-retro no-index no-push no-clean"


class InsertBefore(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        subprocess.run(["git", "-C", self.d, "init", "-q"], check=True)  # plan check wants Predicted paths inside a git repo
        self.w = Path(self.d, ".work", "s")
        Path(self.d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False}), encoding="utf-8")
        self.p("stage-init", "--stage", "s", "--goal", "g", "--flags", FLAGS)

    def p(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", self.d])
        return rc, out.getvalue(), err.getvalue()

    def task(self, n, paths, **kw):
        extra = [x for k, v in kw.items() for x in (f"--{k.replace('_', '-')}", str(v))]
        return self.p("task", "--stage", "s", "--id", str(n), "--title", f"Do {n}", "--paths", paths, "--test", "t", "--done", "ok", *extra)

    def before(self, n, paths, **kw):
        extra = [x for k, v in kw.items() for x in (f"--{k.replace('_', '-')}", str(v))]
        return self.p("task", "--stage", "s", "--before", str(n), "--title", "New", "--paths", paths, "--test", "t", "--done", "ok", *extra)

    def chain(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js", depends="1")
        self.task(3, "c/z.js", depends="2")

    def parsed(self):
        return {t["id"]: (t["wave"], t.get("depends", [])) for t in parse_tasks(str(self.w / "TASKS.md"))}

    def log(self, *events):
        (self.w / "orch.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    def test_renumbers_later_tasks_files_depends_and_graph(self):
        self.chain()
        rc, out, _ = self.before(2, "n/new.js")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.parsed(), {"task-1": (1, []), "task-2": (2, []), "task-3": (3, ["task-1", "task-2"]), "task-4": (4, ["task-3"])})
        self.assertIn("# task-3: Do 2", (self.w / "task-3.md").read_text(encoding="utf-8"))
        self.assertIn("Wave: W3", (self.w / "task-3.md").read_text(encoding="utf-8"))
        self.assertIn("# task-2: New", (self.w / "task-2.md").read_text(encoding="utf-8"))
        self.assertIn("wave 2: task-2 | wave 3: task-3 | wave 4: task-4", (self.w / "TASKS.md").read_text(encoding="utf-8"))

    def test_id_of_a_started_task_is_refused(self):
        self.task(1, "a/x.js")
        self.log({"ev": "dispatch", "task": "task-1", "id": "a1"})
        before = (self.w / "task-1.md").read_text(encoding="utf-8")
        rc, _, err = self.task(1, "b/y.js")
        self.assertNotEqual(rc, 0)
        self.assertIn("already started", err)
        self.assertEqual((self.w / "task-1.md").read_text(encoding="utf-8"), before)
        self.assertEqual(self.task(2, "b/y.js")[0], 0)

    def test_started_task_keeps_its_id_and_new_gets_the_next_free_one(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js")
        self.task(3, "c/z.js")
        self.log({"ev": "dispatch", "task": "task-3", "id": "a1"})
        rc, out, _ = self.before(2, "n/new.js")
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing is renumbered", out)
        self.assertEqual(self.parsed(), {"task-1": (1, []), "task-2": (2, ["task-4"]), "task-3": (1, []), "task-4": (1, [])})

    def test_started_target_gets_the_first_idle_wave_and_lists_started_tasks(self):
        self.chain()
        self.log({"ev": "dispatch", "task": "task-1", "id": "a1"}, {"ev": "dispatch", "task": "task-2", "id": "a2"})
        rc, out, _ = self.before(2, "n/new.js")
        self.assertEqual(rc, 0, out)
        self.assertIn("first wave with no started task; started before it: task-1, task-2", out)
        self.assertEqual(self.parsed(), {"task-1": (1, []), "task-2": (2, ["task-1"]), "task-3": (3, ["task-2"]), "task-4": (3, [])})

    def test_adding_a_task_uses_the_plan_check_overlap_rule(self):
        self.assertEqual(self.task(1, "data/k.jsonl (rows)")[0], 0)
        self.assertEqual(self.task(2, "data/k.jsonl (rows)")[0], 0)  # a shared (rows) file is fine in one wave
        rc, _, err = self.task(3, "data/k.jsonl")  # the whole file clashes with the rows owners
        self.assertEqual(rc, 2, err)
        self.assertIn("share paths", err)

    def test_a_started_step_that_is_not_a_task_does_not_break_the_note(self):
        self.chain()
        self.log({"ev": "skip", "task": "final-test"}, {"ev": "dispatch", "task": "task-1", "id": "a1"}, {"ev": "dispatch", "task": "task-2", "id": "a2"})
        rc, out, err = self.before(2, "n/new.js")
        self.assertEqual(rc, 0, err)
        self.assertIn("started before it: task-1, task-2", out)
        self.assertNotIn("final-test", out)

    def test_refuses_started_dependent_that_must_wait(self):
        self.chain()
        self.log({"ev": "dispatch", "task": "task-3", "id": "a1"})
        before = (self.w / "TASKS.md").read_text(encoding="utf-8")
        rc, _, err = self.before(2, "n/new.js")
        self.assertEqual(rc, 2)
        self.assertIn("task-3 has started", err)
        self.assertEqual(before, (self.w / "TASKS.md").read_text(encoding="utf-8"))

    def test_started_dependent_is_refused(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js")
        self.task(3, "c/z.js", depends="2")
        self.log({"ev": "skip", "task": "task-3"})
        rc, _, err = self.before(2, "n/new.js")
        self.assertEqual(rc, 2)
        self.assertIn("task-3 has started", err)

    def test_cycle_unknown_and_overlap_rejected(self):
        self.chain()
        rc, _, err = self.before(1, "n/new.js", depends="3")
        self.assertEqual((rc, "cycle" in err), (2, True))
        self.assertEqual(self.before(2, "n/new.js", depends="9")[0], 2)
        self.assertEqual(self.before(9, "n/new.js")[0], 2)
        self.task(4, "d/q.js")
        rc, _, err = self.before(4, "a/x.js")
        self.assertEqual((rc, "share paths" in err), (2, True))
        self.assertEqual(sorted(self.parsed()), ["task-1", "task-2", "task-3", "task-4"])

    def test_check_runs_on_the_result(self):
        self.chain()
        rc, out, _ = self.before(3, ", ".join(f"p/f{i}.js" for i in range(30)))
        self.assertEqual(rc, 2)
        self.assertIn("task-3", out)

    def test_wave_reloads_the_list_and_dispatches_the_new_task_first(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js", depends="1")
        a = SimpleNamespace(dir=self.d, stage="s")
        done = [
            {"ev": "dispatch", "task": "task-1", "id": "a1", "agent": "dev", "role": "dev", "model": "haiku", "why": None},
            {"ev": "report", "task": "task-1", "id": "a1", "role": "dev", "verdict": "DONE", "file": "d.md"},
            {"ev": "dispatch", "task": "task-1", "id": "b1", "agent": "reviewer", "role": "reviewer", "model": "haiku", "why": None},
            {"ev": "report", "task": "task-1", "id": "b1", "role": "reviewer", "verdict": "PASS", "file": "r.md"},
        ]
        self.log(*done)
        self.assertEqual([x["task"] for x in graph.decide(a)["dispatches"]], ["task-2"])
        self.assertEqual(self.before(2, "n/new.js")[0], 0)  # waves are running: task-2 has not started
        with mock.patch.object(graph, "run_index", return_value=0):
            self.assertEqual([x["task"] for x in graph.decide(a)["dispatches"]], ["task-2"])
            self.assertIn("New", (self.w / "task-2.md").read_text(encoding="utf-8"))
            blocked = [
                {"ev": "dispatch", "task": "task-2", "id": "a2", "agent": "dev", "role": "dev", "model": "haiku", "why": None},
                {"ev": "report", "task": "task-2", "id": "a2", "role": "dev", "verdict": "BLOCKED", "file": "d.md"},
            ]
            self.log(*done, *blocked)
            r = graph.decide(a)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("held back by them, not started: task-3", r["message"])

    def test_counters_count_the_new_step(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js", depends="1")
        self.before(2, "n/new.js")
        self.log({"ev": "skip", "task": "task-1"})
        c = SimpleNamespace(w=str(self.w), log=str(self.w / "orch.jsonl"), tasks=dict, disp=0, limit=0, cost=0.0)
        line = progress.status_line(c, [])
        self.assertIn("tasks 1/3 done", line)


if __name__ == "__main__":
    unittest.main()
