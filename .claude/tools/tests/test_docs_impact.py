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

    def test_generic_main_is_ignored_but_a_specific_def_is_not(self):
        main_diff = file_diff("a.py", "@@ -1 +1 @@ def main(argv=None):\n-def main():\n+def main(argv=None):\n")
        self.assertEqual(self.check(main_diff, {"docs/m.md": "`main` of b.py\n"}), [])
        foo_diff = file_diff("a.py", "@@ -1 +1 @@ def foo(x):\n-def foo():\n+def foo(x):\n")
        self.assertEqual(len(self.check(foo_diff, {"docs/m.md": "`foo` and `main`\n"})), 1)

    def test_exempt_doc_is_not_flagged(self):
        diff = added_file("a.py")
        docs = {"docs/decisions.md": "see a.py for the parser\n"}
        self.assertEqual(self.check(diff, docs, {DOCS_IMPACT_EXEMPT_KEY: ["docs/decisions.md"]}), [])

    def test_no_changed_path_or_def_is_no_finding(self):
        self.assertEqual(self.check("", {"docs/m.md": "see a.py\n"}), [])


class TestDocsImpactGate(unittest.TestCase):
    def run_task(self, diff, switch=None, docs=None, stage="s"):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/{stage}")
        with open(f"{d}/.work/{stage}/task-1.md", "w") as f:
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

        return testrun.run(stage, "task-1", d, runner=green, scan=lambda *_a: [], diff_fn=lambda *_a: diff)

    def test_stale_doc_is_fail_with_docs_impact_note(self):
        verdict, note = self.run_task(added_file("a.py"))
        self.assertEqual(verdict, testrun.FAIL)
        self.assertTrue(note.startswith("FAIL docs impact:"), note)
        self.assertIn("docs/m.md", note)

    def test_gate_false_skips_check(self):
        verdict, note = self.run_task(added_file("a.py"), switch=False)
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("docs impact", note)

    def test_dict_switch_on_for_named_stage_fails(self):
        verdict, note = self.run_task(added_file("a.py"), switch={"default": False, "s": True}, stage="s")
        self.assertEqual(verdict, testrun.FAIL)
        self.assertIn("FAIL docs impact:", note)

    def test_dict_default_on_covers_unnamed_stage(self):
        verdict, note = self.run_task(added_file("a.py"), switch={"default": True, "s": False}, stage="other")
        self.assertEqual(verdict, testrun.FAIL)
        self.assertIn("FAIL docs impact:", note)

    def test_dict_switch_off_named_stage_skips_check_even_with_default_on(self):
        verdict, note = self.run_task(added_file("a.py"), switch={"default": True, "s": False}, stage="s")
        self.assertEqual(verdict, testrun.DONE)
        self.assertNotIn("docs impact", note)


class TestGateOn(unittest.TestCase):
    def test_bool_is_the_switch_of_every_stage(self):
        self.assertTrue(docs_impact.gate_on(True, "any"))
        self.assertFalse(docs_impact.gate_on(False, "any"))

    def test_dict_names_stages_and_default_covers_the_rest(self):
        setting = {"default": False, "a": True}
        self.assertTrue(docs_impact.gate_on(setting, "a"))
        self.assertFalse(docs_impact.gate_on(setting, "b"))

    def test_dict_without_default_is_on_for_unnamed_stage(self):
        self.assertTrue(docs_impact.gate_on({"a": False}, "b"))
        self.assertFalse(docs_impact.gate_on({"a": False}, "a"))
