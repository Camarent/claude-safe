"""Characterization tests for audit_run.main / run / audit_enrich.main: exact printed output and exit codes."""

import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_enrich as E
from audit import audit_run as R

STAMP = datetime.date.today().isoformat()


def finding(i, sev="warning", rule="r"):
    return {"file": f"f{i}.py", "line": i, "rule": rule, "severity": sev, "message": f"msg{i}"}


def parse_many(out, err):
    return [finding(i, "error" if i % 2 else "warning") for i in range(12)], "sum-line"


def parse_ok(out, err):
    return [], "fine"


def parse_boom(out, err):
    raise ValueError("bad format")


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        with open(os.path.join(self.d, "AUDIT.md"), "w") as fh:
            fh.write("x\n```audit-tools\nmany | . | echo a | pmany\nok | . | echo b | pok\nboom | . | echo c | pboom\ngone | . | nosuchtool_xyz | pok\n```\n")

    def go(self, *args, fn=R.main):
        buf = io.StringIO()
        parsers = {"pmany": parse_many, "pok": parse_ok, "pboom": parse_boom}
        with mock.patch.dict(R.PARSERS, parsers), contextlib.redirect_stdout(buf):
            rc = fn([self.d, *args])
        return rc, buf.getvalue().splitlines()

    def test_many_truncated(self):
        rc, out = self.go("--only", "many")
        self.assertEqual(rc, 1)
        self.assertEqual(out[0], "FINDINGS many 12 (error 6, warning 6)")
        self.assertEqual(out[1], "  f0.py:0 r msg0")
        self.assertEqual(out[10], "  f9.py:9 r msg9")
        self.assertEqual(out[11], "  ... +2 more, see findings.json")
        self.assertEqual(out[12], "  sum-line")
        self.assertTrue(out[13].startswith("findings: "))

    def test_verbose_lists_all(self):
        rc, out = self.go("--only", "many", "--verbose")
        self.assertEqual(rc, 1)
        self.assertEqual(out[12], "  f11.py:11 r msg11")
        self.assertEqual(out[13], "  sum-line")

    def test_ok_and_crash_and_missing(self):
        rc, out = self.go("--only", "ok")
        self.assertEqual((rc, out[0]), (0, "OK ok fine"))
        rc, out = self.go("--only", "boom")
        self.assertEqual(rc, 2)
        self.assertTrue(out[0].startswith("CRASH boom ValueError: bad format (see "))
        rc, out = self.go("--only", "gone")
        self.assertEqual((rc, out[0]), (2, "TOOL-MISSING: gone"))

    def test_unset_variable_is_named_not_missing(self):
        with open(os.path.join(self.d, "AUDIT.md"), "w") as fh:
            fh.write('x\n```audit-tools\nenvy | . | echo "${NOPE_VAR_XYZ:?set it}" | pok\n```\n')
        rc, out = self.go()
        self.assertEqual((rc, out[0]), (2, "TOOL-UNSET: envy needs the environment variable NOPE_VAR_XYZ"))

    def test_jstools_defaults_when_installed(self):
        with open(os.path.join(self.d, "AUDIT.md"), "w") as fh:
            fh.write('x\n```audit-tools\nj | . | echo "${JSTOOLS:?x}" | pok\n```\n')
        env = {k: v for k, v in os.environ.items() if k != "JSTOOLS"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("os.path.isdir", side_effect=lambda p, _r=os.path.isdir: True if p == "/opt/jstools/node_modules" else _r(p)),
        ):
            rc, _out = self.go()
            self.assertEqual(os.environ["JSTOOLS"], "/opt/jstools")
        self.assertEqual(rc, 0)

    def test_unknown_parser_and_counts(self):
        with open(os.path.join(self.d, "AUDIT.md"), "w") as fh:
            fh.write("x\n```audit-tools\nu | . | echo hi | weird\n```\n")
        rc, out = self.go()
        self.assertEqual(rc, 1)
        self.assertTrue(out[0].startswith("FINDINGS u ? UNKNOWN-PARSER"))
        path = out[-1].split("findings: ")[1]
        with open(path) as fh:
            self.assertEqual(json.load(fh), {"counts": {"u": 0}, "findings": []})

    def test_baseline(self):
        base = os.path.join(self.d, "base.json")
        with open(base, "w") as fh:
            json.dump({"findings": [dict(finding(0), tool="many"), dict(finding(99), tool="many")]}, fh)
        rc, out = self.go("--only", "many", "--baseline", base)
        self.assertEqual(rc, 1)
        self.assertIn("BASELINE new 11 fixed 1 unchanged 1", out)
        with open(out[-1].split("findings: ")[1]) as fh:
            self.assertEqual(json.load(fh)["baseline"], {"new": 11, "fixed": 1, "unchanged": 1})

    def test_only_run_never_overwrites_the_consolidated_findings(self):
        _rc, full = self.go("--only", "many")
        consolidated = full[-1].split("findings: ")[1]
        self.assertTrue(consolidated.endswith("-only-many.json"))
        self.assertFalse(consolidated.endswith("-findings.json"))
        _rc, out = self.go()
        main_file = out[-1].split("findings: ")[1]
        self.assertTrue(main_file.endswith("-findings.json"))
        with open(main_file) as fh:
            before = fh.read()
        self.go("--only", "ok")
        with open(main_file) as fh:
            self.assertEqual(fh.read(), before)

    def test_run_verdict(self):
        rc, out = self.go("--only", "many", "--verdict", fn=R.run)
        self.assertEqual((rc, out[0]), (0, "VERDICT: DONE"))
        rc, out = self.go("--only", "boom", "--verdict", fn=R.run)
        self.assertEqual((rc, out[0]), (2, "VERDICT: FAIL"))

    def test_git_failure_keeps_plain_date(self):
        real = R.subprocess.run

        def run(cmd, *a, **kw):
            if cmd[0] == "git":
                raise OSError("no git")
            return real(cmd, *a, **kw)

        with mock.patch.object(R.subprocess, "run", run):
            rc, _out = self.go("--only", "ok")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.d, ".audits", "raw", STAMP + "-only-ok.json")))


class Enrich(unittest.TestCase):
    def run_enrich(self, findings, lp_rc, *extra):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.json")
            with open(path, "w") as fh:
                json.dump({"findings": findings}, fh)
            buf = io.StringIO()
            with mock.patch.object(E, "lp_rc", lp_rc), contextlib.redirect_stdout(buf):
                rc = E.main([d, "--findings", path, *extra])
            with open(path) as fh:
                return rc, buf.getvalue(), json.load(fh)["findings"]

    def test_tags_unused_claims(self):
        fs = [
            {"rule": "F401", "message": "unused function 'helper'", "file": "a.py"},
            {"rule": "x", "message": "something else 'helper'", "file": "a.py"},
            {"rule": "dead", "message": "dead 'other'", "file": "a.py", "check": "kept"},
            {"rule": "dead", "message": "dead code with no quote", "file": "a.py"},
            {"rule": "dead", "message": "dead 'nofile'"},
            {"rule": "dead", "message": "dead 'nolp'", "file": "a.py"},
        ]

        def lp_rc(repo, *a, t=60):
            if a[-1] == "nolp":
                return 1, ""
            return 0, "a.py:1 def\nb.py:5 use\nc.py:7 use\n"

        rc, out, res = self.run_enrich(fs, lp_rc, "--verdict")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("VERDICT: DONE\naudit-enrich: 1 unused-code findings checked in "))
        self.assertEqual(res[0]["check"], "3 refs incl. the definition, 2 in other files: b.py:5, c.py:7")
        self.assertNotIn("check", res[1])
        self.assertEqual(res[2]["check"], "kept")
        self.assertNotIn("check", res[3])
        self.assertNotIn("check", res[4])
        self.assertNotIn("check", res[5])

    def test_refs_variants(self):
        with mock.patch.object(E, "lp_rc", return_value=(0, "a.py:1 def\n")):
            self.assertEqual(E.refs("r", "a.py", "n"), "1 refs incl. the definition, 0 in other files (supports the claim)")
        with mock.patch.object(E, "lp_rc", return_value=(0, "# word-match\nb.py:2 x\n(none)\n")):
            self.assertEqual(E.refs("r", "a.py", "n"), "1 text hits, 1 in other files (unverified word search, not the same symbol for sure)")

    def test_no_findings_file(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(E.main([d]), 0)
        self.assertEqual(buf.getvalue(), "audit-enrich: no findings file\n")

    def test_budget_and_cap(self):
        fs = [{"rule": "dead", "message": f"dead 'n{i}'", "file": "a.py"} for i in range(3)]
        with mock.patch.object(E, "MAX_CHECKS", 2):
            _rc, _out, res = self.run_enrich(fs, lambda *a, t=60: (0, "a.py:1 d\n"))
        self.assertEqual(["check" in f for f in res], [True, True, False])


if __name__ == "__main__":
    unittest.main()
