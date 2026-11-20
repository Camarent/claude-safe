import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import qualityrun
from pipeline.configuration import QUALITY_TOOLS, ConfigError, quality_tools
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp


def repo():
    d = tracked_mkdtemp()
    git(d, "init", "-q")
    with open(f"{d}/a.js", "w") as f:
        f.write("one\ntwo\nthree\n")
    git(d, "add", ".")
    git(d, "commit", "-qm", "x")
    return d


class QualityRun(unittest.TestCase):
    def test_changed_lines_and_new_files(self):
        d = repo()
        with open(f"{d}/a.js", "w") as f:
            f.write("one\nTWO\nthree\n")
        with open(f"{d}/b.rs", "w") as f:
            f.write("x\n")
        ch = qualityrun.changed(d, "HEAD")
        self.assertEqual(ch["a.js"], {2})
        self.assertIsNone(ch["b.rs"])

    def test_tools_by_extension(self):
        t = QUALITY_TOOLS
        self.assertEqual(qualityrun.tools_for({"x.rs": None}, t), ["clippy"])
        self.assertEqual(qualityrun.tools_for({"x.mjs": None, "y.rs": None}, t), ["eslint", "knip", "jscpd", "madge", "clippy"])
        self.assertEqual(qualityrun.tools_for({"x.md": None, "x.py": None}, t), [])
        self.assertEqual(qualityrun.tools_for({"x.py": None}, quality_tools({"quality_tools": {".py": ["ruff"]}})), ["ruff"])
        with self.assertRaises(ConfigError):
            quality_tools({"quality_tools": {".py": "ruff"}})

    def test_python_only_when_configured(self):
        d = repo()
        self.addCleanup(shutil.rmtree, d, True)
        with open(f"{d}/m.py", "w") as f:
            f.write("x=1\n")
        v, text = self.run_q(d, lambda r, n: self.fail("must not run"), tools=("ruff",))
        self.assertIn("no audit tool applies", text)
        with open(f"{d}/pipeline.json", "w") as f:
            f.write('{"quality_tools": {".py": ["ruff"]}}')
        hit = [{"file": "m.py", "line": 1, "tool": "ruff", "rule": "E225", "message": "m"}]
        v, text = self.run_q(d, lambda r, n: hit, tools=("ruff",))
        self.assertEqual(v, "FAIL")
        self.assertIn("m.py:1 [ruff]", text)

    def test_missing_program_is_skipped(self):
        self.assertFalse(qualityrun._installed("no-such-program-xyz --x"))
        self.assertTrue(qualityrun._installed("python3 -V"))

    def test_keep_only_changed_lines(self):
        found = [{"file": "/opt/p/web/a.js", "line": 2}, {"file": "/opt/p/web/a.js", "line": 1}, {"file": "other.js", "line": 2}]
        self.assertEqual(len(qualityrun.keep(found, {"web/a.js": {2}})), 1)

    def run_q(self, d, auditor, tools=("eslint", "clippy")):
        with mock.patch.object(qualityrun, "repo_tools", return_value=list(tools)):
            return qualityrun.run("s", "task-1", d, auditor=auditor)

    def test_fail_clean_and_unavailable(self):
        d = repo()
        with open(f"{d}/a.js", "w") as f:
            f.write("one\nTWO\nthree\n")
        hit = [{"file": "a.js", "line": 2, "tool": "eslint", "rule": "no-x", "message": "bad"}, {"file": "a.js", "line": 3, "tool": "eslint"}]
        v, text = self.run_q(d, lambda r, n: hit)
        self.assertEqual(v, "FAIL")
        self.assertIn("ADVISORY", text)
        self.assertIn("a.js:2 [eslint] no-x bad", text)
        self.assertNotIn("a.js:3", text)
        self.assertEqual(self.run_q(d, lambda r, n: [])[0], "DONE")

        def boom(r, n):
            raise RuntimeError("no tool")

        v, text = self.run_q(d, boom)
        self.assertEqual(v, "DONE")
        self.assertIn("skipped", text)

    def test_nothing_applies(self):
        d = repo()
        with open(f"{d}/n.md", "w") as f:
            f.write("x\n")
        v, text = self.run_q(d, lambda r, n: self.fail("must not run"))
        self.assertEqual(v, "DONE")
        self.assertIn("no audit tool applies", text)

    def test_tool_not_in_audit_md_is_skipped(self):
        d = repo()
        with open(f"{d}/a.js", "w") as f:
            f.write("x\n")
        v, _ = self.run_q(d, lambda r, n: self.fail("must not run"), tools=("clippy",))
        self.assertEqual(v, "DONE")


class RunAuditTest(unittest.TestCase):
    """qualityrun.run_audit with a fake audit_run process."""

    def fake(self, stdout="", stderr="", exc=None):
        return mock.patch.object(qualityrun.subprocess, "run", side_effect=exc, return_value=mock.Mock(stdout=stdout, stderr=stderr))

    def test_reads_the_findings_file_named_in_the_output(self):
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "findings.json")
            with open(fp, "w", encoding="utf-8") as f:
                f.write('{"findings": [{"file": "a.js", "line": 2}]}')
            with self.fake(stdout=f"x\nfindings: {fp}\n") as run:
                self.assertEqual(qualityrun.run_audit(d, ["eslint", "knip"]), [{"file": "a.js", "line": 2}])
            self.assertEqual(run.call_args.args[0][-2:], ["--only", "eslint,knip"])

    def test_no_findings_line_raises_with_the_output_tail(self):
        with self.fake(stdout="", stderr="tool crashed"), self.assertRaisesRegex(RuntimeError, "no findings file: tool crashed"):
            qualityrun.run_audit(".", ["eslint"])

    def test_process_errors_and_unreadable_file_raise(self):
        with self.fake(exc=OSError("no python")), self.assertRaisesRegex(RuntimeError, "no python"):
            qualityrun.run_audit(".", ["eslint"])
        with self.fake(stdout="findings: /nonexistent/f.json\n"), self.assertRaises(RuntimeError):
            qualityrun.run_audit(".", ["eslint"])


if __name__ == "__main__":
    unittest.main()
