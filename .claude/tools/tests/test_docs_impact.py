"""Docs impact: a doc that cites a changed file or top-level def must change in the same job diff. testrun runs it after the cite check; `docs_impact_gate` false in pipeline.json switches it off."""

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


class TestDocsImpactCheck(unittest.TestCase):
    def check(self, diff, docs, cfg=NO_EXEMPT):
        wt = worktree(docs)
        return docs_impact.check(wt, diff, docs_impact.doc_files(wt, "docs"), cfg)

    def test_cited_path_changed_and_doc_untouched_fails_naming_doc(self):
        msgs = self.check(added_file("a.py"), {"docs/m.md": "see a.py for the parser\n"})
        self.assertEqual(len(msgs), 1)
        self.assertIn("docs/m.md", msgs[0])
        self.assertIn("a.py", msgs[0])

    def test_cited_path_with_doc_changed_passes(self):
        diff = added_file("a.py") + added_file("docs/m.md", "more\n")
        self.assertEqual(self.check(diff, {"docs/m.md": "see a.py for the parser\n"}), [])

    def test_path_line_cite_of_changed_path_fails(self):
        msgs = self.check(added_file("pkg/a.py"), {"docs/m.md": "the loop is at pkg/a.py:12\n"})
        self.assertEqual(len(msgs), 1)
        self.assertIn("pkg/a.py", msgs[0])

    def test_changed_top_level_def_backticked_in_doc_fails(self):
        diff = file_diff("a.py", "@@ -1 +1 @@ def foo(x):\n-def foo():\n+def foo(x):\n")
        msgs = self.check(diff, {"docs/m.md": "call `foo` to start\n"})
        self.assertEqual(len(msgs), 1)
        self.assertIn("`foo`", msgs[0])
        self.assertIn("docs/m.md", msgs[0])

    def test_indented_method_named_like_cited_symbol_is_ignored(self):
        diff = file_diff("a.py", "@@ -3 +3 @@ class K:\n-    def bar(self):\n+    def bar(self, x):\n")
        self.assertEqual(self.check(diff, {"docs/m.md": "call `bar` or `K`\n"}), [])

    def test_symbol_under_minimum_length_is_ignored(self):
        diff = file_diff("a.py", "@@ -1 +1 @@ def ab():\n-def ab():\n+def ab(x):\n")
        self.assertEqual(self.check(diff, {"docs/m.md": "call `ab`\n"}), [])

    def test_exempt_doc_is_not_flagged(self):
        diff = added_file("a.py")
        docs = {"docs/decisions.md": "see a.py for the parser\n"}
        self.assertEqual(self.check(diff, docs, {DOCS_IMPACT_EXEMPT_KEY: ["docs/decisions.md"]}), [])

    def test_no_changed_path_or_def_is_no_finding(self):
        self.assertEqual(self.check("", {"docs/m.md": "see a.py\n"}), [])


class TestDocsImpactGate(unittest.TestCase):
    def run_task(self, diff, switch=None, docs=None):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/s")
        with open(f"{d}/.work/s/task-1.md", "w") as f:
            f.write("# t\nTest: true\n")
        os.makedirs(f"{d}/docs")
        for rel, text in (docs or {"docs/m.md": "see a.py for the parser\n"}).items():
            with open(f"{d}/{rel}", "w") as f:
                f.write(text)
        if switch is not None:
            with open(f"{d}/pipeline.json", "w") as f:
                json.dump({DOCS_IMPACT_KEY: switch}, f)

        def green(argv, **_kw):
            return subprocess.CompletedProcess(argv, 0, "", "")

        return testrun.run("s", "task-1", d, runner=green, scan=lambda *_a: [], diff_fn=lambda *_a: diff)

    def test_stale_doc_is_fail_with_docs_impact_note(self):
        verdict, note = self.run_task(added_file("a.py"))
        self.assertEqual(verdict, testrun.FAIL)
        self.assertTrue(note.startswith("FAIL docs impact:"), note)
        self.assertIn("docs/m.md", note)

    def test_gate_false_skips_check(self):
        verdict, note = self.run_task(added_file("a.py"), switch=False)
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("docs impact", note)
