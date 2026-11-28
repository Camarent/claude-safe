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
from pipeline import cli_plan, plan_check, plan_files, plan_rules, plan_testwrap
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp


def tid(n):
    """Task id of number n (`task-<n>`), built here so assertion lines carry no literal id."""
    return f"task-{n}"


class UnlistedPrereq(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.ents = [
            {"id": tid(1), "wave": 1, "depends": []},
            {"id": tid(2), "wave": 2, "depends": [tid(1)]},
            {"id": tid(3), "wave": 2, "depends": []},
            {"id": tid(4), "wave": 3, "depends": [tid(2)]},
        ]

    def lint(self, n, text):
        Path(self.d, tid(n) + ".md").write_text(f"# {tid(n)}: title\nWave: W1\n{text}\n", encoding="utf-8")
        return plan_rules.check_unlisted_prereq(self.d, next(e for e in self.ents if e["id"] == tid(n)), self.ents)

    def test_a_same_wave_task_named_in_the_brief_without_a_dependency_is_flagged(self):
        got = self.lint(2, "Needs the change of task-3 in tools/sim.")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("prereq: " + tid(2)))
        self.assertIn("--depends 3", got[0])
        self.assertEqual(plan_check.errors_of(got), [])  # a warning, never an error

    def test_an_earlier_wave_task_a_dependency_and_a_transitive_one_are_fine(self):
        text_a = f"Builds on {tid(1)} (earlier wave)."
        text_b = f"Uses the result of {tid(2)} and, through it, {tid(1)}."
        self.assertEqual(self.lint(2, text_a), [])
        self.assertEqual(self.lint(4, text_b), [])

    def test_pattern_lines_addenda_and_other_phase_references_do_not_count(self):
        for text in ("Pattern: task-3 style", "see task-3 of phase 01 for the idea", "Brief.\n## ADDENDUM 2026-11-17 10:00 UTC\n\nneeds task-3"):
            self.assertEqual(self.lint(2, text), [], text)

    def test_a_task_folder_inside_a_path_is_not_a_mention(self):
        in_path = f"Work in /w/.wt/x/{tid(3)}/repo only."
        plain = f"Needs {tid(3)}."
        self.assertEqual(self.lint(2, in_path), [])
        self.assertEqual(len(self.lint(2, plain)), 1)

    def test_the_own_id_unknown_ids_and_the_heading_do_not_count(self):
        text = f"This is {tid(3)} and not {tid(99)}."
        self.assertEqual(self.lint(3, text), [])


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
        self.assertEqual(self.depends()[tid(2)], [tid(1)])
        self.assertEqual(self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "1")[0], 0)  # again: nothing doubled
        self.assertEqual(self.depends()[tid(2)], [tid(1)])

    def test_a_same_wave_dependency_moves_the_task_and_its_dependents_to_a_later_wave(self):
        rc, err = self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "3")  # task-3 sits in the same wave 2
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.depends()[tid(2)], [tid(3)])
        waves = {e["id"]: e["wave"] for e in plan_files.read_entries(os.path.join(self.d, ".work", "s"))}
        self.assertEqual((waves[tid(3)], waves[tid(2)]), (2, 3))
        self.assertIn("Wave: W3", Path(self.d, ".work", "s", tid(2) + ".md").read_text(encoding="utf-8"))
        self.assertEqual(plan_check.errors_of(plan_check.check_stage(os.path.join(self.d, ".work", "s"))[1]), [])

    def test_refuses_a_cycle_an_unknown_and_itself_and_a_started_task_that_would_have_to_move(self):
        for dep in ("99", "2"):
            rc, err = self.p("edit-task", "--stage", "s", "--id", "2", "--depends", dep)
            self.assertEqual(rc, 2, (dep, err))
        self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "1")
        rc, err = self.p("edit-task", "--stage", "s", "--id", "1", "--depends", "2")  # 2 depends on 1
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.depends()[tid(1)], [])
        Path(self.d, ".work", "s", "orch.jsonl").write_text(json.dumps({"ev": "dispatch", "task": tid(3), "id": "a1"}) + "\n", encoding="utf-8")
        rc, err = self.p("edit-task", "--stage", "s", "--id", "3", "--depends", "2")  # task-3 started and would have to wait for task-2's wave
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.depends()[tid(3)], [])

    def test_the_output_says_what_changed_and_notes_the_dependency_mode(self):
        self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "1")
        self.assertIn(tid(2) + " now depends on " + tid(1), self.out)
        self.assertIn("dependency mode", self.out)  # no task had a dependency before
        self.p("edit-task", "--stage", "s", "--id", "3", "--depends", "1")
        self.assertNotIn("dependency mode", self.out)


class TestWrap(unittest.TestCase):
    """plan check `testwrap:`: a Test line matching a project `test_wrap` rule must carry the rule's wrapper text."""

    RULE = {"match": "tests/db", "require": "KATA_CONFIG", "use": "KATA_CONFIG=<config> scripts/dev.sh test"}

    def setUp(self):
        self.d = tracked_mkdtemp()
        self.ws = tracked_mkdtemp()

    def cfg(self, **kw):
        Path(self.ws, "pipeline.json").write_text(json.dumps(kw), encoding="utf-8")

    def check(self, test_line):
        Path(self.d, tid(1) + ".md").write_text(f"# {tid(1)}: title\nWave: W1\nTest: {test_line}\n", encoding="utf-8")
        return plan_testwrap.check_test_wrap(self.d, tid(1), self.ws)

    def test_a_db_test_without_the_wrapper_names_the_wrapper(self):
        self.cfg(test_wrap=[self.RULE])
        got = self.check("cs-qrun -- scripts/dev.sh test tests/db/test_store.py")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("testwrap:"))
        self.assertIn(tid(1), got[0])
        self.assertIn("KATA_CONFIG=<config> scripts/dev.sh test", got[0])

    def test_a_db_test_with_the_wrapper_is_fine(self):
        self.cfg(test_wrap=[self.RULE])
        self.assertEqual(self.check("cs-qrun -- env KATA_CONFIG=ci scripts/dev.sh test tests/db/test_store.py"), [])

    def test_a_non_matching_line_without_the_wrapper_is_fine(self):
        self.cfg(test_wrap=[self.RULE])
        self.assertEqual(self.check("cs-qrun -- scripts/dev.sh test tests/unit/test_x.py"), [])

    def test_no_rules_or_malformed_rules_give_no_problem(self):
        self.assertEqual(self.check("cs-qrun -- scripts/dev.sh test tests/db/test_store.py"), [])
        for raw in ("tests/db", [7], [{"match": "tests/db", "use": "x"}], [{"match": "([", "require": "x", "use": "x"}]):
            self.cfg(test_wrap=raw)
            self.assertEqual(self.check("cs-qrun -- scripts/dev.sh test tests/db/test_store.py"), [])

    def test_the_rule_is_read_from_the_project_settings(self):
        self.cfg(test_wrap=[self.RULE])
        self.assertTrue(self.check("cs-qrun -- scripts/dev.sh test tests/db/test_store.py"))
        self.cfg()
        self.assertEqual(self.check("cs-qrun -- scripts/dev.sh test tests/db/test_store.py"), [])


class TestWrapStage(unittest.TestCase):
    """plan_check.check_stage runs the `testwrap:` rule on a real stage dir (the wiring in _task_file_problems)."""

    RULE = {"match": "tests/db", "require": "KATA_CONFIG", "use": "KATA_CONFIG=<config> scripts/dev.sh test"}

    def setUp(self):
        self.root = tracked_mkdtemp()
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)
        Path(self.root, "pipeline.json").write_text(json.dumps({"test_wrap": [self.RULE]}), encoding="utf-8")

    def stage_problems(self, test_line):
        Path(self.d, "TASKS.md").write_text(
            "## Tasks\n- [ ] task-1 one - owner: haiku - review: wave W1, haiku\n\n## Graph\nwave 1: task-1\n",
            encoding="utf-8",
        )
        Path(self.d, "task-1.md").write_text(f"# task-1: one\nWave: W1\nPredicted paths: src/a.py\nTest: {test_line}\n", encoding="utf-8")
        _, probs = plan_check.check_stage(self.d, repo=self.root)
        return [p for p in probs if p.startswith(plan_testwrap.TEST_WRAP_PREFIX)]

    def test_a_db_test_without_the_wrapper_is_a_problem(self):
        got = self.stage_problems("cs-qrun -- scripts/dev.sh test tests/db/test_store.py")
        self.assertEqual(len(got), 1)
        self.assertIn(tid(1), got[0])
        self.assertIn("KATA_CONFIG=<config> scripts/dev.sh test", got[0])

    def test_a_db_test_with_the_wrapper_gives_none(self):
        self.assertEqual(self.stage_problems("cs-qrun -- env KATA_CONFIG=ci scripts/dev.sh test tests/db/test_store.py"), [])


if __name__ == "__main__":
    unittest.main()
