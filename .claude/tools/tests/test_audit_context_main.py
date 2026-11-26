"""Characterization tests for audit_context.main (synthetic git repo, lp patched) and audit_report.main."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_context as C
from audit import audit_report as RP
from tests.support.gitrepo import git as _git


def _make_repo(d):
    _git(d, "init", "-q", "-b", "master")
    with open(os.path.join(d, "a.py"), "w") as fh:
        fh.write("x = 1\n")
    _git(d, "add", ".")
    _git(d, "commit", "-qm", "one")
    _git(d, "checkout", "-qb", "dev")
    with open(os.path.join(d, "a.py"), "a") as fh:
        fh.write("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
    with open(os.path.join(d, "test_a.py"), "w") as fh:
        fh.write("def test_x():\n    pass\n")
    with open(os.path.join(d, "notes.md"), "w") as fh:
        fh.write("hello\n")
    _git(d, "add", ".")
    _git(d, "commit", "-qm", "two")


OUTLINES = {
    "a.py": "2:1  function foo\n6:1  function bar\n1:1  function old\n",
    "test_a.py": "1:1  function test_x\n",
    "notes.md": "",
}


def fake_lp(repo, *a, t=60):
    return OUTLINES.get(a[-1], "") if a[:2] == ("nav", "outline") else ""


def fake_uses(repo, f, sym):
    return {"foo": (["b.py", "c.py", "d.py", "e.py", "f.py", "g.py"], False), "bar": ([], True)}.get(sym, ([], False))


class Main(unittest.TestCase):
    def run_main(self, extra=(), model=False):
        with tempfile.TemporaryDirectory() as d:
            _make_repo(d)
            out = os.path.join(d, "sub", "ctx.md")
            buf = io.StringIO()
            with mock.patch.object(C, "lp", fake_lp), mock.patch.object(C, "uses_of", fake_uses), contextlib.redirect_stdout(buf):
                rc = C.main([d, "--base", "master", "--head", "dev", "--out", out, *extra, *([] if model else ["--no-model"])])
            with open(out) as fh:
                return rc, buf.getvalue(), fh.read().splitlines()

    def test_full(self):
        rc, stdout, lines = self.run_main(["--verdict"])
        self.assertEqual(rc, 0)
        self.assertTrue(stdout.startswith("VERDICT: DONE\naudit-context: 2 of 3 files with outline, 2 symbols -> "))
        self.assertEqual(lines[0], "# Audit context (master..dev): 3 changed files")
        self.assertIn("a.py +2-7  new: foo@2 bar@6", lines)
        self.assertIn("  foo: 6 files: b.py, c.py, d.py, e.py +2", lines)
        self.assertIn("  ~bar: 0 files (no other use: possibly unused)", lines)
        self.assertIn("test_a.py +1-2  (test)", lines)
        self.assertTrue(lines[-1].startswith("Changed files with no outline"))
        self.assertTrue(lines[-1].endswith("notes.md"))

    def test_no_verdict_prefix(self):
        _rc, stdout, _lines = self.run_main()
        self.assertTrue(stdout.startswith("audit-context:"))

    def test_budget_cut(self):
        with mock.patch.object(C, "BUDGET_S", -1):
            _rc, _out, lines = self.run_main()
        self.assertEqual(lines[-1], "(stopped after -1s: remaining files not listed)")

    def test_more_files_note(self):
        with mock.patch.object(C, "MAX_FILES", 1):
            _rc, _out, lines = self.run_main()
        self.assertIn("(+2 more changed code files not listed)", lines)

    def test_refresh_index_called_with_workspace(self):
        from search import ctx_config

        with mock.patch.object(ctx_config, "ensure_index") as ei:
            self.run_main(["--workspace", "/ws"])
        ei.assert_called_once_with("/ws")

    def test_refresh_index_failure_is_swallowed(self):
        from search import ctx_config

        with mock.patch.object(ctx_config, "ensure_index", side_effect=RuntimeError("boom")):
            rc, _out, lines = self.run_main(["--workspace", "/ws"])
        self.assertEqual(rc, 0)
        self.assertIn("a.py +2-7  new: foo@2 bar@6", lines)

    def test_related_section_and_event(self):
        found = ["- tests/test_a.py: uses foo (a.py)"]
        with mock.patch.object(C, "_related", return_value=(["", "## Related, unchanged (x)", *found], "ok")):
            _rc, _out, lines = self.run_main(model=True)
        self.assertIn(found[0], lines)

    def test_related_down_is_one_line_note(self):
        with mock.patch("localmodel.related_files.related_unchanged", return_value=None):
            _rc, _out, lines = self.run_main(model=True)
        self.assertEqual(lines[-1], "(related files: skipped, local model down)")

    def test_related_error_is_swallowed(self):
        with mock.patch("localmodel.related_files.related_unchanged", side_effect=RuntimeError("x")):
            rc, _out, lines = self.run_main(model=True)
        self.assertEqual(rc, 0)
        self.assertEqual(lines[-1], "(related files: skipped, local model down)")

    def test_no_model_flag_leaves_section_out(self):
        with mock.patch.object(C, "_related") as related:
            self.run_main()
        related.assert_not_called()

    def test_is_test(self):
        self.assertTrue(C.is_test("src/app.spec.js"))
        self.assertFalse(C.is_test("src/a.py"))


class Report(unittest.TestCase):
    FINDINGS = [
        {"tool": "ruff", "severity": "warning", "rule": "F401", "file": "a.py", "line": 3, "message": "unused import"},
        {"tool": "ruff", "severity": "warning", "rule": "F401", "file": "b.py", "line": 4, "message": "unused", "check": "0 refs"},
        {"tool": "mypy", "severity": "error", "rule": "arg", "file": "a.py", "message": "m" * 150},
    ]

    def run_report(self, data, *args):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.json")
            with open(p, "w") as fh:
                json.dump(data, fh)
            buf = io.StringIO()
            with mock.patch.object(sys, "argv", ["audit_report", p, *args]), contextlib.redirect_stdout(buf):
                RP.main()
            return buf.getvalue().splitlines()

    def test_default_group_rule(self):
        out = self.run_report({"findings": self.FINDINGS})
        self.assertEqual(out[0], "total 3; by tool: {'ruff': 2, 'mypy': 1}")
        self.assertEqual(out[1], "by severity: {'warning': 2, 'error': 1}")
        self.assertEqual(out[2], "top 10 by rule:")
        self.assertEqual(out[3], "      2 ruff F401")
        self.assertEqual(out[4], "      1 mypy arg")

    def test_group_file_and_top(self):
        out = self.run_report(self.FINDINGS, "--group", "file", "--top", "1")
        self.assertEqual(out[2:], ["top 1 by file:", "      1 ruff a.py"])

    def test_tool_filter_and_clean(self):
        self.assertEqual(self.run_report(self.FINDINGS, "--tool", "nope"), ["total 0: clean"])
        out = self.run_report(self.FINDINGS, "--tool", "mypy")
        self.assertEqual(out[0], "total 1; by tool: {'mypy': 1}")

    def test_list(self):
        out = self.run_report(self.FINDINGS, "--list", "--top", "3")
        self.assertEqual(out[2], "a.py:3 ruff F401 unused import")
        self.assertEqual(out[3], "b.py:4 ruff F401 unused  [check: 0 refs]")
        self.assertEqual(out[4], "a.py: mypy arg " + "m" * 100)


if __name__ == "__main__":
    unittest.main()
