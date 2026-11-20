"""pipeline/starter_tools.py: draft audit-tools block by marker file, its lines load and name known parsers, new parsers (synthetic data)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_parsers, audit_run
from pipeline import extra_tools, starter_tools
from tests.support.tmpdirs import tracked_mkdtemp


def repo_with(*files):
    d = tracked_mkdtemp()
    for f in files:
        open(os.path.join(d, f), "w").close()
    return d


class StarterTest(unittest.TestCase):
    def test_each_marker_picks_its_language(self):
        for marker, lang in (("pyproject.toml", "Python"), ("Cargo.toml", "Rust"), ("package.json", "JavaScript"), ("CMakeLists.txt", "C++")):
            self.assertEqual([x[0] for x in starter_tools.detect(repo_with(marker))], [lang])

    def test_mixed_repo_gets_every_language_and_unknown_gets_nothing(self):
        self.assertEqual([x[0] for x in starter_tools.detect(repo_with("Cargo.toml", "package.json"))], ["Rust", "JavaScript"])
        self.assertEqual(starter_tools.draft(repo_with("README.md")), "")

    def test_draft_loads_with_audit_run_and_every_parser_exists(self):
        d = repo_with("pyproject.toml", "Cargo.toml", "package.json", "CMakeLists.txt")
        open(os.path.join(d, "AUDIT.md"), "w").write(starter_tools.draft(d))
        tools = audit_run.load_tools(d)
        self.assertGreaterEqual(len(tools), 10)
        for name, _cwd, cmd, parser in tools:
            self.assertIn(parser, audit_parsers.PARSERS, name)
            self.assertTrue(cmd, name)

    def test_c_plus_plus_draft_has_the_three_tools_and_no_clangd(self):
        text = starter_tools.draft(repo_with("CMakeLists.txt"))
        self.assertNotIn("clangd", text)  # owner 2026-11-01: clangd is project-side, never installed in the container
        self.assertEqual([t[0] for t in self.load(text)], ["cppcheck", "clang-tidy", "lizard"])

    def load(self, text):
        d = repo_with()
        open(os.path.join(d, "AUDIT.md"), "w").write(text)
        return audit_run.load_tools(d)

    def test_note_proposes_a_block_only_when_none_exists(self):
        d = repo_with("Cargo.toml")
        note = extra_tools.audit_tools_note(os.path.join(d, "AUDIT.md"))
        self.assertIn("no audit-tools block", note)
        self.assertIn("cargo-audit", note)
        open(os.path.join(d, "AUDIT.md"), "w").write(starter_tools.draft(d))
        self.assertEqual(extra_tools.audit_tools_note(os.path.join(d, "AUDIT.md")), "")
        self.assertNotIn("```audit-tools", extra_tools.audit_tools_note(os.path.join(repo_with("README.md"), "AUDIT.md")))


class ParserTest(unittest.TestCase):
    def test_ruff(self):
        out = '[{"filename":"a.py","code":"F401","message":"unused","location":{"row":3,"column":1}}]'
        self.assertEqual(audit_parsers.ruff(out, "")[0][0]["line"], 3)

    def test_npm_audit(self):
        out = '{"vulnerabilities":{"lodash":{"severity":"high","via":[{"source":1,"title":"Prototype pollution"}]},"x":{"severity":"low","via":["lodash"]}}}'
        res = audit_parsers.npm_audit(out, "")[0]
        self.assertEqual([r["severity"] for r in res], ["high", "low"])
        self.assertIn("Prototype pollution", res[0]["message"])

    def test_clang_tidy_keeps_diagnostics_and_skips_notes(self):
        out = "a.cpp:4:5: warning: use nullptr [modernize-use-nullptr]\na.cpp:4:5: note: expanded here\nb.cpp:9:1: error: bad [clang-diagnostic-error]\n"
        res = audit_parsers.clang_tidy(out, "")[0]
        self.assertEqual(
            [(r["file"], r["line"], r["severity"], r["rule"]) for r in res],
            [("a.cpp", 4, "warning", "modernize-use-nullptr"), ("b.cpp", 9, "error", "clang-diagnostic-error")],
        )

    def test_cppcheck_reads_stderr_template_lines_and_skips_noise(self):
        err = "Checking a.cpp ...\na.cpp:12:error:nullPointer:Null pointer dereference\nb.cpp:3:style:unusedFunction:unused\n"
        res = audit_parsers.cppcheck("", err)[0]
        self.assertEqual(
            [(r["file"], r["line"], r["severity"], r["rule"]) for r in res], [("a.cpp", 12, "error", "nullPointer"), ("b.cpp", 3, "warning", "unusedFunction")]
        )


if __name__ == "__main__":
    unittest.main()
