"""Docs impact scope: a doc is never the target of the check (doc-to-doc cites, docs-only diffs), and a stage's TASKS.md `Docs gate: off` line switches the check off for that stage."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testrun
from pipeline import docs_impact, plan_check, plan_docs_impact
from pipeline.configuration import DOCS_IMPACT_EXEMPT_KEY, DOCS_IMPACT_KEY, DOCS_IMPACT_PLAN_PREFIX
from tests.support.tmpdirs import tracked_mkdtemp

NO_EXEMPT: dict[str, list[str]] = {DOCS_IMPACT_EXEMPT_KEY: []}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def file_diff(path, body):
    """A `git diff -U0`-like text for one file: body is its hunks."""
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{body}"


def added_file(path, text="x = 1\n"):
    return file_diff(path, f"@@ -0,0 +1 @@\n+{text}")


def worktree(docs):
    """A worktree dir holding each doc (relative path -> text) under docs/."""
    d = tracked_mkdtemp()
    for rel, text in docs.items():
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with open(os.path.join(d, rel), "w") as f:
            f.write(text)
    return d


class TestDocsScope(unittest.TestCase):
    def check(self, diff, docs, cfg=NO_EXEMPT):
        wt = worktree(docs)
        return docs_impact.check(wt, diff, docs_impact.doc_files(wt, "docs"), cfg)

    def test_doc_cites_changed_doc_unchanged_citer_is_not_flagged(self):
        # C1: the target is a changed doc, so the unchanged citing doc is no finding
        diff = added_file("docs/target.md", "more\n")
        self.assertEqual(self.check(diff, {"docs/target.md": "more\n", "docs/citer.md": "see docs/target.md for the parser\n"}), [])

    def test_doc_cites_changed_doc_by_bare_name_is_not_flagged(self):
        diff = added_file("docs/target.md", "more\n")
        self.assertEqual(self.check(diff, {"docs/target.md": "more\n", "docs/citer.md": "see target.md here\n"}), [])

    def test_doc_cites_changed_doc_does_not_hide_a_changed_code_target(self):
        # the unchanged doc's cite of a changed .py still counts when a changed .md is also cited
        diff = added_file("docs/target.md", "more\n") + added_file("a.py")
        msgs = self.check(diff, {"docs/target.md": "more\n", "docs/citer.md": "see docs/target.md and a.py\n"})
        self.assertEqual(len(msgs), 1)
        self.assertIn("a.py", msgs[0])
        self.assertNotIn("target.md", msgs[0])

    def test_docs_only_diff_is_no_finding(self):
        # C2: every changed file is a doc: the docs-only exemption
        diff = added_file("docs/a.md", "x\n") + added_file("docs/b.md", "y\n")
        docs = {"docs/a.md": "x\n", "docs/b.md": "y\n", "docs/c.md": "see a.md and b.md and the parser\n"}
        self.assertEqual(self.check(diff, docs), [])

    def test_docs_only_diff_with_changed_def_in_doc_is_no_finding(self):
        # a def-looking line inside a changed doc is not a symbol the diff changes
        diff = file_diff("docs/a.md", "@@ -1 +1 @@ def foo(x):\n-def foo():\n+def foo(x):\n")
        self.assertEqual(self.check(diff, {"docs/a.md": "x\n", "docs/c.md": "call `foo` to start\n"}), [])

    def test_decoy_doc_cites_changed_py_and_changed_md_only_py_reported(self):
        # C3: a doc cites a changed .py and a changed .md: only the .py is reported
        diff = added_file("a.py") + added_file("docs/target.md", "more\n")
        msgs = self.check(diff, {"docs/target.md": "more\n", "docs/m.md": "see a.py and docs/target.md\n"})
        self.assertEqual(len(msgs), 1)
        self.assertIn("a.py", msgs[0])
        self.assertIn("docs/m.md", msgs[0])
        self.assertNotIn("target.md", msgs[0])


class TestDocsStageGate(unittest.TestCase):
    def run_task(self, diff, tasks_text=None, switch=None, docs=None, stage="s"):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/{stage}")
        with open(f"{d}/.work/{stage}/task-1.md", "w") as f:
            f.write("# t\nTest: true\n")
        if tasks_text is not None:
            with open(f"{d}/.work/{stage}/TASKS.md", "w") as f:
                f.write(tasks_text)
        os.makedirs(f"{d}/docs")
        for rel, text in (docs or {"docs/m.md": "see a.py for the parser\n"}).items():
            with open(f"{d}/{rel}", "w") as f:
                f.write(text)
        if switch is not None:
            with open(f"{d}/pipeline.json", "w") as f:
                json.dump({DOCS_IMPACT_KEY: switch}, f)

        def green(argv, **_kw):
            return subprocess.CompletedProcess(argv, 0, "", "")

        return testrun.run(stage, "task-1", d, runner=green, scan=lambda *_a: [], diff_fn=lambda *_a: diff)

    def test_tasks_line_docs_gate_off_skips_check(self):
        verdict, note = self.run_task(added_file("a.py"), tasks_text="# plan\nDocs gate: off\n")
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("docs impact", note)

    def test_tasks_line_docs_gate_off_is_case_insensitive_and_wins_over_default_on(self):
        verdict, note = self.run_task(added_file("a.py"), tasks_text="docs gate: OFF\n", switch=True)
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("docs impact", note)

    def test_no_tasks_line_keeps_pipeline_default_on(self):
        verdict, note = self.run_task(added_file("a.py"), tasks_text="# plan\nflags: docs\n", switch=True)
        self.assertEqual(verdict, testrun.FAIL)
        self.assertIn("FAIL docs impact:", note)

    def test_no_tasks_file_keeps_pipeline_default_on(self):
        verdict, note = self.run_task(added_file("a.py"))
        self.assertEqual(verdict, testrun.FAIL)
        self.assertIn("FAIL docs impact:", note)

    def test_pipeline_switch_off_still_skips_without_tasks_line(self):
        verdict, note = self.run_task(added_file("a.py"), switch=False)
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("docs impact", note)

    def test_doc_to_doc_cite_gives_no_fail_note(self):
        # C5: a doc cites a changed doc: no FAIL note asks for a doc edit
        diff = added_file("docs/target.md", "more\n")
        verdict, note = self.run_task(diff, docs={"docs/target.md": "more\n", "docs/citer.md": "see docs/target.md\n"})
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("docs impact", note)


class TestPlanOff(unittest.TestCase):
    def test_docs_gate_off_line(self):
        self.assertTrue(docs_impact.plan_off("a\nDocs gate: off\n"))

    def test_other_values_and_absence_are_not_off(self):
        self.assertFalse(docs_impact.plan_off("Docs gate: on\n"))
        self.assertFalse(docs_impact.plan_off("note: Docs gate: off here\n"))
        self.assertFalse(docs_impact.plan_off(""))


MAIN = f"task-{2}"  # ids of the plan-check fixtures, built so no literal id sits in an assert
DOC_TASK = f"task-{3}"


class TestPlanDocsImpact(unittest.TestCase):
    def check(self, tasks, docs, tasks_extra="", cfg=None):
        """tasks: task id -> Predicted paths; docs: repo path -> text. -> plan_docs_impact.check_docs_impact for the main task."""
        ws = tracked_mkdtemp()
        d = os.path.join(ws, ".work", "s")
        write(os.path.join(d, "TASKS.md"), "# plan\n" + tasks_extra + "".join(f"- {t} t - review: wave W1\n" for t in tasks))
        for t, paths in tasks.items():
            write(os.path.join(d, t + ".md"), "# t\nPredicted paths: " + ", ".join(paths) + "\n")
        for rel, text in docs.items():
            write(os.path.join(ws, rel), text)
        if cfg is not None:
            write(os.path.join(ws, "pipeline.json"), json.dumps(cfg))
        return plan_docs_impact.check_docs_impact(d, MAIN, ws)

    def test_doc_citing_predicted_path_not_in_predicted_docs_is_flagged(self):
        # C1: docs/m.md cites a.py which the main task changes; docs/m.md is in no Predicted paths
        msgs = self.check({MAIN: ["a.py"]}, {"docs/m.md": "see a.py for the parser\n"})
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].startswith(DOCS_IMPACT_PLAN_PREFIX))
        self.assertIn(MAIN, msgs[0])
        self.assertIn("docs/m.md cites a.py", msgs[0])

    def test_doc_listed_in_own_predicted_paths_is_clear(self):
        # C2: the main task lists the citing doc itself
        self.assertEqual(self.check({MAIN: ["a.py", "docs/m.md"]}, {"docs/m.md": "see a.py\n"}), [])

    def test_doc_only_task_of_stage_listing_citing_doc_is_clear(self):
        # C2: a doc-only task of the stage lists the citing doc
        self.assertEqual(self.check({MAIN: ["a.py"], DOC_TASK: ["docs/m.md"]}, {"docs/m.md": "see a.py\n"}), [])

    def test_doc_citing_no_predicted_path_is_clear(self):
        # C2 decoy: no doc cites a.py, so no finding
        self.assertEqual(self.check({MAIN: ["a.py"]}, {"docs/n.md": "see b.py only\n"}), [])

    def test_docs_only_task_is_no_finding(self):
        self.assertEqual(self.check({MAIN: ["docs/m.md"]}, {"docs/n.md": "see a.py\n"}), [])

    def test_tasks_line_docs_gate_off_is_clear(self):
        # C4: the stage's TASKS.md turns the gate off
        self.assertEqual(self.check({MAIN: ["a.py"]}, {"docs/m.md": "see a.py\n"}, tasks_extra="Docs gate: off\n"), [])

    def test_pipeline_gate_off_is_clear(self):
        # C4: pipeline.json switches the gate off for every stage
        self.assertEqual(self.check({MAIN: ["a.py"]}, {"docs/m.md": "see a.py\n"}, cfg={DOCS_IMPACT_KEY: False}), [])

    def test_exempt_doc_is_not_read(self):
        self.assertEqual(self.check({MAIN: ["a.py"]}, {"docs/m.md": "see a.py\n"}, cfg={DOCS_IMPACT_EXEMPT_KEY: ["docs/m.md"]}), [])


class TestPlanCheckWiring(unittest.TestCase):
    """plan_check.check_stage runs check_docs_impact on each task file: the problem reaches the stage's check result."""

    def stage(self, task_paths):
        root = tracked_mkdtemp()
        d = os.path.join(root, ".work", "s")
        os.makedirs(d)
        write(os.path.join(root, "docs", "m.md"), "see a.py for the parser\n")
        write(os.path.join(d, "TASKS.md"), "## Tasks\n- [ ] task-1 one - owner: haiku - review: wave W1, haiku\n")
        write(os.path.join(d, "task-1.md"), f"# t\nPredicted paths: {task_paths}\n")
        return plan_check.check_stage(d, repo=root)[1]

    def test_stage_reports_docs_impact_for_citing_doc_not_predicted(self):
        probs = self.stage("a.py")
        self.assertTrue(
            any(p.startswith(DOCS_IMPACT_PLAN_PREFIX) and "task-1" in p and "docs/m.md" in p for p in probs),
            probs,
        )

    def test_stage_is_clear_when_citing_doc_is_predicted(self):
        probs = self.stage("a.py, docs/m.md")
        self.assertEqual([p for p in probs if p.startswith(DOCS_IMPACT_PLAN_PREFIX)], [])
