"""Docs impact scope: a doc is never the target of the check (doc-to-doc cites, docs-only diffs), and a stage's TASKS.md `Docs gate: off` line switches the check off for that stage."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testrun
from pipeline import docs_impact
from pipeline.configuration import DOCS_IMPACT_EXEMPT_KEY, DOCS_IMPACT_KEY
from tests.support.tmpdirs import tracked_mkdtemp

NO_EXEMPT: dict[str, list[str]] = {DOCS_IMPACT_EXEMPT_KEY: []}


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
