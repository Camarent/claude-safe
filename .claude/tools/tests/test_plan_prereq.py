"""plan check `prereq:` warning and `cs-plan edit-task --depends` (synthetic stages)."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan, plan_check, plan_files, plan_rules
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp


class UnlistedPrereq(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.ents = [
            {"id": "task-1", "wave": 1, "depends": []},
            {"id": "task-2", "wave": 2, "depends": ["task-1"]},
            {"id": "task-3", "wave": 2, "depends": []},
            {"id": "task-4", "wave": 3, "depends": ["task-2"]},
        ]

    def lint(self, tid, text):
        Path(self.d, tid + ".md").write_text(f"# {tid}: title\nWave: W1\n{text}\n", encoding="utf-8")
        return plan_rules.check_unlisted_prereq(self.d, next(e for e in self.ents if e["id"] == tid), self.ents)

    def test_a_same_wave_task_named_in_the_brief_without_a_dependency_is_flagged(self):
        got = self.lint("task-2", "Needs the change of task-3 in tools/sim.")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("prereq: task-2"))
        self.assertIn("--depends 3", got[0])
        self.assertEqual(plan_check.errors_of(got), [])  # a warning, never an error

    def test_an_earlier_wave_task_a_dependency_and_a_transitive_one_are_fine(self):
        self.assertEqual(self.lint("task-2", "Builds on task-1 (earlier wave)."), [])
        self.assertEqual(self.lint("task-4", "Uses the result of task-2 and, through it, task-1."), [])

    def test_pattern_lines_addenda_and_other_phase_references_do_not_count(self):
        for text in ("Pattern: task-3 style", "see task-3 of phase 01 for the idea", "Brief.\n## ADDENDUM 2026-11-17 10:00 UTC\n\nneeds task-3"):
            self.assertEqual(self.lint("task-2", text), [], text)

    def test_the_own_id_unknown_ids_and_the_heading_do_not_count(self):
        self.assertEqual(self.lint("task-3", "This is task-3 and not task-99."), [])


class EditTaskDepends(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        subprocess.run(["git", "-C", self.d, "init", "-q"], check=True)
        Path(self.d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False}), encoding="utf-8")
        self.p("stage-init", "--stage", "s", "--goal", "g", "--flags", "no-merge no-test no-audit no-retro no-index no-push no-clean")
        for n, wave, paths in ((1, 1, "a/x.js"), (2, 2, "b/y.js"), (3, 2, "c/z.js")):
            self.p("task", "--stage", "s", "--id", str(n), "--title", f"T{n}", "--paths", paths, "--test", "t", "--done", "ok", "--wave", str(wave))

    def p(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", self.d])
        self.out = out.getvalue()
        return rc, err.getvalue()

    def depends(self):
        return {e["id"]: e["depends"] for e in plan_files.read_entries(os.path.join(self.d, ".work", "s"))}

    def test_adds_a_dependency_and_keeps_the_task_file(self):
        rc, err = self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.depends()["task-2"], ["task-1"])
        self.assertEqual(self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "1")[0], 0)  # again: nothing doubled
        self.assertEqual(self.depends()["task-2"], ["task-1"])

    def test_a_same_wave_dependency_moves_the_task_and_its_dependents_to_a_later_wave(self):
        rc, err = self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "3")  # task-3 sits in the same wave 2
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.depends()["task-2"], ["task-3"])
        waves = {e["id"]: e["wave"] for e in plan_files.read_entries(os.path.join(self.d, ".work", "s"))}
        self.assertEqual((waves["task-3"], waves["task-2"]), (2, 3))
        self.assertIn("Wave: W3", Path(self.d, ".work", "s", "task-2.md").read_text(encoding="utf-8"))
        self.assertEqual(plan_check.errors_of(plan_check.check_stage(os.path.join(self.d, ".work", "s"))[1]), [])

    def test_refuses_a_cycle_an_unknown_and_itself_and_a_started_task_that_would_have_to_move(self):
        for dep in ("99", "2"):
            rc, err = self.p("edit-task", "--stage", "s", "--id", "2", "--depends", dep)
            self.assertEqual(rc, 2, (dep, err))
        self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "1")
        rc, err = self.p("edit-task", "--stage", "s", "--id", "1", "--depends", "2")  # 2 depends on 1
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.depends()["task-1"], [])
        Path(self.d, ".work", "s", "orch.jsonl").write_text(json.dumps({"ev": "dispatch", "task": "task-3", "id": "a1"}) + "\n", encoding="utf-8")
        rc, err = self.p("edit-task", "--stage", "s", "--id", "3", "--depends", "2")  # task-3 started and would have to wait for task-2's wave
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.depends()["task-3"], [])

    def test_the_output_says_what_changed_and_notes_the_dependency_mode(self):
        self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "1")
        self.assertIn("task-2 now depends on task-1", self.out)
        self.assertIn("dependency mode", self.out)  # no task had a dependency before
        self.p("edit-task", "--stage", "s", "--id", "3", "--depends", "1")
        self.assertNotIn("dependency mode", self.out)


if __name__ == "__main__":
    unittest.main()
