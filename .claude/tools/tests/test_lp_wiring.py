"""Synthetic-data tests for the lp wiring: search counting, audit_enrich tags, plan context file, wave WARN. No real repo, no model."""

import argparse
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_enrich
from localmodel import plan_context
from pipeline import cli_wave, lp_share, plan_run
from pipeline.configuration import tool_cmd
from tests.support.tmpdirs import tracked_mkdtemp


class CodeNameTest(unittest.TestCase):
    def test_names_from_many_languages_and_non_names_skipped(self):
        from codenames import code_name as c

        for tok, want in (
            ("ns::Class::method()", "method"),
            ("crate::util::parse", "parse"),
            ("obj.method", "method"),
            ("pkg.Func", "Func"),
            ("Klass#meth", "meth"),
            ("vec!", "vec"),
            ("std::vector<int>::size", "size"),
            ("~Widget", "Widget"),
            ("snake_case_fn", "snake_case_fn"),
            ("Größe", "Größe"),
        ):
            self.assertEqual(c(tok), want, tok)
        for tok in ("src/main.rs", "--flag", "two words", "123", "", "a/b::c"):
            self.assertIsNone(c(tok), tok)


class HitFileTest(unittest.TestCase):
    def test_hit_lines_of_any_language(self):
        from codenames import hit_file as h

        self.assertEqual(h("src/a.cpp:12-30  keyword"), "src/a.cpp")
        self.assertEqual(h("crates/x/src/lib.rs:7  symbol fn f"), "crates/x/src/lib.rs")
        self.assertEqual(h("C:/odd/p.py:3-4 x"), "C:/odd/p.py")
        for bad in ("(none)", "", "# note: x", "VERDICT: DONE"):
            self.assertIsNone(h(bad), bad)


class ExtraLeadsTest(unittest.TestCase):
    def test_symbols_and_outline_for_marked_names_and_predicted_files(self):
        from pipeline import briefs
        from search import leadparts

        d = tracked_mkdtemp()
        os.makedirs(f"{d}/src")
        open(f"{d}/src/w.cpp", "w").close()
        table = {("nav", "symbols"): "src/w.cpp:3  method Widget::draw\n", ("nav", "outline"): "3:1  method draw\n"}

        def fake(repo, *a, t=60):
            return table.get(a[:2], "")

        text = "# t\nPredicted paths: src/w.cpp, include/\nFix `Widget::draw()` and `--flag`\n"
        with mock.patch.object(leadparts, "lp", fake):
            out = briefs.extra_leads(d, text)
        self.assertIn("`draw`: src/w.cpp:3", out)
        self.assertIn("src/w.cpp:", out)
        self.assertIn("src/w.cpp: draw@3", out)
        with mock.patch.object(leadparts, "lp", lambda *a, **k: ""):
            self.assertEqual(briefs.extra_leads(d, text), "")  # nothing from lp: no text, no failure


class SearchCountTest(unittest.TestCase):
    def test_lp_and_local_py_count_as_local_grep_as_grep(self):
        def k(cmd):
            return lp_share.classify("Bash", {"command": cmd})

        self.assertEqual(k('lp find "where is x"'), "local:find")
        self.assertEqual(k("cd d && lp nav refs f.py 3"), "local:nav-refs")
        self.assertEqual(k(tool_cmd("search/local.py") + " nav def f.py g"), "local:nav-def")
        self.assertEqual(k("grep -rn foo src/"), "grep")
        self.assertIsNone(k("lp status"))


class EnrichTest(unittest.TestCase):
    def run_enrich(self, answer):
        d = tracked_mkdtemp()
        f = os.path.join(d, "f.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "findings": [
                        {"rule": "unused-function", "message": "unused function 'ns::Timer::start()'", "file": "a.js"},
                        {"rule": "style", "message": "long line 'x'", "file": "a.js"},
                    ]
                },
                fh,
            )
        with mock.patch.object(audit_enrich, "refs", return_value=answer), redirect_stdout(io.StringIO()):
            audit_enrich.main([d, "--findings", f])
        return json.loads(Path(f).read_text(encoding="utf-8"))["findings"]

    def test_unused_claim_gets_a_check_tag_other_findings_do_not(self):
        fl = self.run_enrich("1 refs incl. the definition, 0 in other files (supports the claim)")
        self.assertIn("supports", fl[0]["check"])
        self.assertNotIn("check", fl[1])

    def test_no_answer_leaves_finding_untagged(self):
        self.assertNotIn("check", self.run_enrich(None)[0])


class PlanContextFileTest(unittest.TestCase):
    def args(self):
        d = tracked_mkdtemp()
        return argparse.Namespace(dir=d, feature="f", stage="f")

    def test_names_the_file_it_wrote_and_never_raises(self):
        a = self.args()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PLAN_NO_CONTEXT", None)
        with (
            mock.patch.object(plan_context, "goal_context", return_value="# ctx\n"),
            mock.patch.object(plan_run, "feature_dir", return_value=os.path.join(a.dir, ".work", "f")),
        ):
            self.assertIn("plan-context.md", plan_run.code_context(a, "goal"))
            self.assertEqual(Path(os.path.join(a.dir, ".work", "f", "plan-context.md")).read_text(encoding="utf-8"), "# ctx\n")
        with mock.patch.object(plan_context, "goal_context", side_effect=RuntimeError("x")):
            self.assertEqual(plan_run.code_context(a, "goal"), "")


class WaveWarnTest(unittest.TestCase):
    def test_warns_when_model_is_down_and_still_runs(self):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/s")
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch("search.strata.strata_up", return_value=False),
            mock.patch.object(cli_wave, "run_loop", return_value=0),
            redirect_stdout(out),
        ):
            os.environ.pop("STRATA", None)
            rc = cli_wave.main(["run", "--stage", "s", "--dir", d])
            self.assertEqual(os.environ.get("STRATA"), "0")
        self.assertEqual(rc, 0)
        self.assertIn("preflight", out.getvalue())


if __name__ == "__main__":
    unittest.main()
