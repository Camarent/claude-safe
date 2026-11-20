import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_parsers as P
from audit import audit_run as R


class Parsers(unittest.TestCase):
    def test_clippy(self):
        line = json.dumps(
            {
                "reason": "compiler-message",
                "message": {
                    "level": "warning",
                    "message": "too long",
                    "code": {"code": "clippy::too_many_lines"},
                    "spans": [{"is_primary": True, "file_name": "a.rs", "line_start": 7}],
                },
            }
        )
        f, _ = P.clippy("noise\n" + line + '\n{"reason":"build-finished"}', "")
        self.assertEqual(f, [P.F("a.rs", 7, "clippy::too_many_lines", "warning", "too long")])

    def test_eslint(self):
        d = [{"filePath": "x.js", "messages": [{"ruleId": "no-undef", "severity": 2, "line": 3, "message": "u"}]}]
        f, _ = P.eslint(json.dumps(d), "")
        self.assertEqual((f[0]["rule"], f[0]["severity"], f[0]["line"]), ("no-undef", "error", 3))

    def test_bandit_and_pip_audit(self):
        f, _ = P.bandit(
            json.dumps({"results": [{"filename": "b.py", "line_number": 2, "test_id": "B101", "issue_severity": "HIGH", "issue_text": "assert"}]}), ""
        )
        self.assertEqual((f[0]["severity"], f[0]["rule"]), ("high", "B101"))
        f, _ = P.pip_audit(json.dumps({"dependencies": [{"name": "p", "version": "1", "vulns": [{"id": "CVE-1", "description": "d"}]}]}), "")
        self.assertEqual(f[0]["rule"], "CVE-1")

    def test_cargo_audit_deny_machete(self):
        f, _ = P.cargo_audit(
            json.dumps(
                {"vulnerabilities": {"list": [{"advisory": {"id": "RUSTSEC-1", "title": "t"}, "package": {"name": "c", "version": "1"}}]}, "warnings": {}}
            ),
            "",
        )
        self.assertEqual(f[0]["rule"], "RUSTSEC-1")
        dl = json.dumps(
            {"type": "diagnostic", "fields": {"severity": "error", "message": "m", "code": "rejected", "labels": [{"span": "foldhash", "line": 1}]}}
        )
        f, _ = P.cargo_deny("", dl)
        self.assertEqual(f[0]["rule"], "rejected")
        f, _ = P.machete("cargo-machete found the following unused dependencies in /r:\ncrate -- /r/Cargo.toml:\n\tserde\n", "")
        self.assertEqual((f[0]["file"], f[0]["message"]), ("/r/Cargo.toml", "unused dependency serde"))

    def test_js_tools(self):
        f, _ = P.madge('[["a.js","b.js"],[]]', "")
        self.assertEqual(len(f), 1)
        f, _ = P.knip(json.dumps({"files": ["u.js"], "issues": [{"file": "a.js", "exports": [{"name": "x", "line": 4}]}]}), "")
        self.assertEqual([x["rule"] for x in f], ["unused-file", "unused-exports"])
        f, s = P.jscpd(
            json.dumps(
                {
                    "duplicates": [{"lines": 12, "firstFile": {"name": "a", "start": 1}, "secondFile": {"name": "b"}}],
                    "statistics": {"total": {"percentage": 2.5}},
                }
            ),
            "",
        )
        self.assertEqual((len(f), s), (1, "duplication 2.5%"))

    def test_text_tools(self):
        f, _ = P.vulture("a.py:12: unused function 'g' (60% confidence)\n", "")
        self.assertEqual((f[0]["file"], f[0]["line"]), ("a.py", 12))
        f, _ = P.lizard("a.c:5: warning: fn has 80 NLOC, 20 CCN, 1 token, 2 PARAM, 90 length, 0 ND\n", "")
        self.assertEqual(f[0]["line"], 5)
        f, _ = P.gitleaks(json.dumps([{"File": "k", "StartLine": 3, "RuleID": "aws", "Description": "key"}]), "")
        self.assertEqual(f[0]["rule"], "aws")

    def test_coverage(self):
        _, s = P.llvm_cov(json.dumps({"data": [{"totals": {k: {"percent": 50} for k in ("lines", "functions", "regions")}}]}), "")
        self.assertIn("lines 50.0%", s)
        _, s = P.node_cov("ℹ all files | 85.2 | 70.0 | 80.0 |\n", "")
        self.assertEqual(s, "line 85.2% branch 70.0% funcs 80.0%")


class Runner(unittest.TestCase):
    def fnd(self, tool, file, rule, msg, line=1):
        return {"tool": tool, "file": file, "line": line, "rule": rule, "severity": "warning", "message": msg}

    def test_key_ignores_line(self):
        self.assertEqual(R.key(self.fnd("t", "f", "r", "m", 1)), R.key(self.fnd("t", "f", "r", "m", 99)))
        self.assertNotEqual(R.key(self.fnd("t", "f", "r", "m")), R.key(self.fnd("t", "f", "r2", "m")))

    def test_diff(self):
        a, b, c = (self.fnd("t", "f", "r", m) for m in "abc")
        self.assertEqual(R.diff([a, b], [b, c]), {"new": 1, "fixed": 1, "unchanged": 1})
        self.assertEqual(R.diff([a, a], [a]), {"new": 1, "fixed": 0, "unchanged": 1})

    def test_missing_target(self):
        self.assertEqual(R.main(["/nonexistent_dir_xyz"]), 2)

    def test_raw_named_by_sha(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "-C", d, "init", "-q"], check=True)
            subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            sha = subprocess.run(["git", "-C", d, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
            with open(os.path.join(d, "AUDIT.md"), "w") as fh:
                fh.write('x\n```audit-tools\nv | . | printf "" | vulture\n```\n')
            R.main([d])
            self.assertTrue(os.path.exists(os.path.join(d, ".audits", "raw", __import__("datetime").date.today().isoformat() + "-" + sha + "-findings.json")))

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AUDIT.md"), "w") as fh:
                fh.write(
                    "x\n```audit-tools\nv | . | printf \"a.py:3: unused function 'f' (60%% confidence)\\n\" | vulture\n"
                    "n | . | nosuchtool_xyz | vulture\nu | . | echo hi | weird\n```\n"
                )
            self.assertEqual(R.main([d, "--only", "v"]), 1)
            self.assertEqual(R.main([d, "--only", "n"]), 2)
            self.assertEqual(R.main([d, "--only", "zzz"]), 3)
            self.assertEqual(R.main([d, "--only", "u"]), 1)

    def test_hung_tool_is_a_crash_not_a_hang(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "raw")
            os.makedirs(raw)
            with mock.patch.object(R.subprocess, "run", side_effect=subprocess.TimeoutExpired("bash", 1)):
                st, fl, summ = R.run_tool(d, "t", ".", "echo hi", "none", raw, "s")
            self.assertEqual((st, fl), ("crash", []))
            self.assertIn("timed out", summ)

    def test_missing_cwd_is_a_missing_tool(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AUDIT.md"), "w") as fh:
                fh.write("x\n```audit-tools\nm | /nonexistent_cwd_xyz | echo hi | vulture\n```\n")
            self.assertEqual(R.main([d]), 2)


if __name__ == "__main__":
    unittest.main()
