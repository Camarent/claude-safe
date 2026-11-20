"""Characterization tests for plan_context: top_line per file type, digest groups, model_files cache, goal/stage sections, CLI. No lp, no Strata."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import plan_context as pc
from pipeline import stage_recall as sr
from search import ctx_config as cc
from search import leadparts, strata
from tests.support.tmpdirs import tracked_mkdtemp


def write(root, rel, text):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


_NO_EXPLORE = mock.patch("localmodel.explore.explore", return_value=None)  # the explorer would call a real Strata


def setUpModule():
    _NO_EXPLORE.start()


def tearDownModule():
    _NO_EXPLORE.stop()


class TopLine(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

    def test_by_file_type(self):
        cases = {
            "a.py": '#!/usr/bin/env python3\n"""Does the thing.\n\nmore"""\nx = 1\n',
            "b.py": "import os\ndef test_first():\n    pass\n",
            "c.py": "import os\n",
            "d.js": "/** Cart maths. */\nfunction f() {}\n",
            "e.sh": "#!/bin/sh\n# Runs the build\nmake\n",
            "f.js": "const x = 1;\ndescribe('cart totals work', () => {});\n",
            "g.js": "const x = 1;\n",
            "h.md": "# Title here\ntext\n",
            "i.md": "no heading\n",
            "k.json": '{"a": 1, "b": 2}',
            "l.json": json.dumps({str(i): i for i in range(20)}),
            "m.json": "[1, 2, 3]",
            "n.json": "7",
            "o.json": "{bad",
            "p.txt": "plain\n",
        }
        for name, text in cases.items():
            write(self.d, name, text)
        got = {n: pc.top_line(self.d, n) for n in cases}
        self.assertEqual(got["a.py"], "Does the thing.")
        self.assertEqual(got["b.py"], "first test test_first")
        self.assertEqual(got["c.py"], "")
        self.assertEqual(got["d.js"], "Cart maths.")
        self.assertEqual(got["e.sh"], "Runs the build")
        self.assertEqual(got["f.js"], "first test: cart totals work")
        self.assertEqual(got["g.js"], "")
        self.assertEqual(got["h.md"], "Title here")
        self.assertEqual(got["i.md"], "")
        self.assertEqual(got["k.json"], "keys: a, b")
        self.assertEqual(got["l.json"], "object, 20 entries")
        self.assertEqual(got["m.json"], "list of 3")
        self.assertEqual(got["n.json"], "")
        self.assertEqual(got["o.json"], "")
        self.assertEqual(got["p.txt"], "")
        self.assertEqual(pc.top_line(self.d, "missing.py"), "")

    def test_empty_docstring_falls_to_test_title(self):
        write(self.d, "q.py", '"""   """\ndef test_x(): pass\n')
        self.assertEqual(pc.top_line(self.d, "q.py"), "first test test_x")


class Digest(unittest.TestCase):
    def test_groups_and_overflow(self):
        d = tracked_mkdtemp()
        write(d, "README.md", "# Readme\nsee a.py for details\n")
        write(d, "a.py", '"""A module."""\n')
        files = [f"m{i}.py" for i in range(27)] + ["a.py", "tests/test_a.py", "tests/test_b.py", "docs/x.md", "notes.md"] + [f"data{i}.json" for i in range(10)]
        for f in files:
            if not os.path.exists(os.path.join(d, f)):
                write(d, f, '{"k": 1}' if f.endswith(".json") else "# T\n" if f.endswith(".md") else "")
        # a.py is 28th code file: past DESCRIBED, a bare path
        out = pc.digest(d, files)
        self.assertEqual(out[0], "### code (most-named first)")
        self.assertIn("- a.py", out)
        self.assertIn("### tests", out)
        self.assertIn("- tests/: test_a.py, test_b.py", out)
        self.assertIn("### docs", out)
        self.assertIn("- docs/x.md - T", out)
        self.assertIn("- data0.json - keys: k", out)
        self.assertIn("- data9.json", out)  # past the cap of 8: bare path

    def test_described_code_with_doc_cite(self):
        d = tracked_mkdtemp()
        write(d, "README.md", "# Readme\nsee a.py for details\n")
        write(d, "a.py", '"""A module."""\n')
        out = pc.digest(d, ["a.py"])
        self.assertEqual(out, ["### code (most-named first)", "- a.py - A module.\n    README.md:2: see a.py for details"])

    def test_empty(self):
        self.assertEqual(pc.digest(tracked_mkdtemp(), []), [])


class Helpers(unittest.TestCase):
    def test_ctx_config_bad_file(self):
        d = tracked_mkdtemp()
        self.assertEqual(pc.ctx_config(d), {**pc.CTX_DEFAULTS, "workspace": d})
        write(d, "pipeline.json", '{"context": {"per_section": 3}}')
        self.assertEqual(pc.ctx_config(d)["per_section"], 3)
        write(d, "pipeline.json", '{"context": 5}')
        self.assertEqual(pc.ctx_config(d), {**pc.CTX_DEFAULTS, "workspace": d})

    def test_pick_root_finds_nested_repo(self):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/course/.git")
        os.makedirs(f"{d}/course/src")
        self.assertEqual(pc.pick_root(d, "edit course/src/x.py now"), f"{d}/course")
        self.assertEqual(pc.pick_root(d, "nothing here"), d)

    def test_pick_roots_root_first_then_each_named_nested(self):
        d = tracked_mkdtemp()
        for c in ("a", "b", "c"):
            os.makedirs(f"{d}/ws/{c}/.git")
        os.makedirs(f"{d}/.git")
        got = pc.pick_roots(d, "fix ws/a/x.py and ws/c/y.py")
        self.assertEqual(got, [d, f"{d}/ws/a", f"{d}/ws/c"])
        self.assertEqual(pc.pick_roots(d, "nothing"), [d])

    def test_all_recall_counts_nested_repo_against_its_own_section(self):
        import subprocess as sp

        d = tracked_mkdtemp()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

        def git(r, *a):
            sp.run(["git", "-C", r, *a], check=True, capture_output=True, env=env)

        for r in (d, f"{d}/ws/n"):
            os.makedirs(r, exist_ok=True)
            git(r, "init", "-q", "-b", "master")
            write(r, "a.py", "1\n")
            git(r, "add", ".")
            git(r, "commit", "-qm", "x")
            git(r, "checkout", "-qb", "dev")
            write(r, "a.py", "2\n")
            git(r, "commit", "-qam", "y")
        cfg = {**pc.CTX_DEFAULTS, "index": ["ws/*"]}
        pack = "## s\n### code\n- a.py\n\n# === repo n (nested, paths relative to it) ===\n## s\n"
        orig = sr.pick_roots
        with mock.patch.object(sr, "pick_roots", lambda r, t, c=None: [r, f"{r}/ws/n"] if r == d else orig(r, t, c)):
            self.assertEqual(pc.all_recall(d, "master", "dev", pack, cfg), (1, 2, ["n:a.py"]))

    def test_doc_mentions_per_file_limit(self):
        d = tracked_mkdtemp()
        write(d, "README.md", "a.py one\na.py two\na.py three\n")
        self.assertEqual(pc.doc_mentions(d, ["a.py"], per_file=2), {"a.py": ["README.md:1: a.py one", "README.md:2: a.py two"]})

    def test_group_of(self):
        self.assertEqual([pc.group_of(f) for f in ("a.py", "tests/x.py", "r.md", "d.json")], ["code", "tests", "docs", "data"])

    def test_code_files(self):
        ctx = "## x\n### code (most-named first)\n- a.py - doc\n- b.py\n### tests\n- t/: x\n"
        self.assertEqual(pc.code_files(ctx), ["a.py", "b.py"])

    def test_context_paths_and_pack_paths(self):
        ctx = "## s\n### code\n- a.py - d\n### tests\n- t/: x.py, y.py\n## other\n- z\n"
        self.assertEqual(pc.context_paths(ctx), ["a.py", "t/x.py", "t/y.py"])
        self.assertEqual(pc.pack_paths(["a", "b"]), ["- a", "- b"])
        many = [f"d/{i}.py" for i in range(9)]
        self.assertEqual(pc.pack_paths(many), ["- d/ (9): 0.py, 1.py, 2.py, 3.py +5"])

    def test_ensure_index(self):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/r1/.git")
        os.makedirs(f"{d}/r2")
        calls = []
        with mock.patch.object(cc, "lp", lambda r, *a, **k: calls.append((r, a))):
            done = pc.ensure_index(d, {"index": ["r*"]})
        self.assertEqual(calls, [(f"{d}/r1", ("index",))])
        self.assertEqual([r for r, _ in done], [f"{d}/r1"])


class ModelFilesCache(unittest.TestCase):
    def setUp(self):
        self.repo = tracked_mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=False)
        for f in ("tools/a.py", "tools/b.py"):
            write(self.repo, f, "")
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(strata, "strata_up", lambda: True).start()
        self.calls = []

    def answer(self, reply):
        def call(p, max_tokens=0):
            self.calls.append(p)
            return reply

        mock.patch.object(strata, "strata_call", call).start()

    def test_cache_hit_second_run_and_per_report(self):
        self.answer('noise {"files": ["tools/b.py", "tools/b.py", "tools/a.py"]}')
        text = "first section has six words here"
        per: list = []
        got = pc.model_files(self.repo, text, pc.CTX_DEFAULTS, per=per)
        self.assertEqual(got, ["tools/b.py", "tools/a.py"])
        self.assertEqual(per, [(text, ["tools/b.py", "tools/a.py"], False)])
        self.assertTrue(os.path.isfile(f"{self.repo}/.code-index/ctx-cache.json"))
        per2: list = []
        pc.model_files(self.repo, text, pc.CTX_DEFAULTS, per=per2)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(per2[0][2])
        pc.model_files(self.repo, text, pc.CTX_DEFAULTS, use_cache=False)
        self.assertEqual(len(self.calls), 2)

    def test_garbage_answer_is_not_cached(self):
        self.answer("I cannot")
        self.assertEqual(pc.model_files(self.repo, "a b c d e f", {**pc.CTX_DEFAULTS, "batch": False}), [])
        self.assertEqual(json.loads(Path(f"{self.repo}/.code-index/ctx-cache.json").read_text(encoding="utf-8")), {})

    def test_odd_items_in_model_answer_do_not_crash_non_explore_path(self):  # same item rules as the batch path: {"path"} dicts and strings only
        self.answer('{"files": [{"path": "tools/b.py"}, 3, "tools/a.py", {"x": 1}]}')
        got = pc.model_files(self.repo, "a b c d e f", {**pc.CTX_DEFAULTS, "batch": False})
        self.assertEqual(got, ["tools/b.py", "tools/a.py"])

    def test_unreadable_cache_is_ignored_and_votes_rank(self):
        write(self.repo, ".code-index/ctx-cache.json", "{broken")
        replies = iter(['{"files": ["tools/a.py"]}', '{"files": ["tools/b.py", "tools/a.py"]}'])
        mock.patch.object(strata, "strata_call", lambda p, max_tokens=0: next(replies)).start()
        got = pc.model_files(self.repo, "one section has now six words\ntwo section has now six words", {**pc.CTX_DEFAULTS, "batch": False})
        self.assertEqual(got, ["tools/a.py", "tools/b.py"])  # a.py named twice


class GoalSections(unittest.TestCase):
    def test_all_sections_with_model_names(self):
        repo = tracked_mkdtemp()
        write(repo, "src/a.py", '"""A."""\n')
        table = {
            ("find", "tests for"): "tests/test_a.py:1-3  keyword\ntests/test_a.py:4-5  keyword\nsrc/a.py:1-2 k\n",
            ("find",): "src/a.py:1-20  symbol function make\nsrc/a.py:30-40 other\n",
            ("nav", "symbols"): "src/a.py:10  function make\n",
            ("nav", "outline"): "10:1  function make\n",
            ("index",): "",
        }

        def fake(r, *a, t=60):
            for k, v in table.items():
                if len(a) >= len(k) and all(a[i].startswith(k[i]) for i in range(len(k))):
                    return v
            return ""

        with mock.patch.object(pc, "lp", fake), mock.patch.object(leadparts, "lp", fake), mock.patch.object(pc, "model_files", lambda *a: ["src/a.py"]):
            out = pc.goal_context(repo, "Change `make` so it rejects empty names")
        self.assertIn("## lp find\n    src/a.py:1-20", out)
        self.assertIn("## outline of the top files", out)
        self.assertEqual(out.count("- tests/test_a.py"), 1)
        self.assertIn("- src/a.py - A.", out)
        self.assertIn("## outline of the first named files", out)
        self.assertIn("(built in ", out)

    def test_no_named_files_says_offline(self):
        with (
            mock.patch.object(pc, "lp", lambda *a, **k: ""),
            mock.patch.object(leadparts, "lp", lambda *a, **k: ""),
            mock.patch.object(pc, "model_files", lambda *a: []),
        ):
            out = pc.goal_context(tracked_mkdtemp(), "x")
        self.assertIn("(local model offline or silent: none)", out)


class StageRecall(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

        def git(*a):
            return subprocess.run(["git", "-C", self.d, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)

        git("init", "-q", "-b", "master")
        write(self.d, "a.py", "x\n")
        write(self.d, "b.py", "x\n")
        git("add", "-A")
        git("commit", "-qm", "init")
        write(self.d, "a.py", "y\n")
        write(self.d, "b.py", "y\n")
        git("add", "-A")
        git("commit", "-qm", "work")
        self.sd = os.path.join(self.d, ".work", "s")
        write(self.sd, "task-1.md", "Predicted paths: a.py\nDone: yes\n")

    def test_missing_stage_dir(self):
        out = pc.stage_context(self.d, os.path.join(self.d, "nope"), "HEAD~1", "HEAD")
        self.assertIn("0 planned tasks, 2 changed files", out)
        self.assertIn("## Predicted paths with no change\n(none)", out)

    def test_recall_section_and_metrics(self):
        write(self.sd, "plan-context.md", "## s\n### code\n- a.py - d\n")
        out = pc.stage_context(self.d, self.sd, "HEAD~1", "HEAD")
        self.assertIn("## task-1.md\nPredicted paths: a.py", out)
        self.assertIn(
            "## Planner context recall: 1 of 2 modified files were listed in plan-context.md (1 files named, precision 100%, mode batch)\n- missed: b.py", out
        )
        rec = json.loads(Path(self.sd, "metrics.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((rec["ev"], rec["stage"], rec["hits"], rec["total"], rec["missed"]), ("context-recall", "s", 1, 2, ["b.py"]))

    def test_precision_and_mode_logged(self):
        write(self.sd, "plan-context.md", "# Code context (by script and a local model that explored the repos)\n## s\n### code\n- a.py - d\n- z.py\n")
        pc.stage_context(self.d, self.sd, "HEAD~1", "HEAD")
        rec = json.loads(Path(self.sd, "metrics.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((rec["hits"], rec["named"], rec["precision"], rec["mode"]), (1, 2, 0.5, "explore"))

    def test_context_file_of_the_feature_above_a_phase(self):
        phase = os.path.join(self.sd, "phases", "01-x")
        os.makedirs(phase)
        write(self.sd, "plan-context.md", "")
        self.assertEqual(pc.find_context_file(phase), os.path.join(self.sd, "plan-context.md"))
        self.assertEqual(pc.find_context_file(self.d), "")

    def test_metrics_failure_is_swallowed(self):
        write(self.sd, "plan-context.md", "")
        import pipeline.util

        with mock.patch.object(pipeline.util, "append_jsonl", side_effect=OSError("disk")):
            out = pc.stage_context(self.d, self.sd, "HEAD~1", "HEAD")
        self.assertIn("## Planner context recall: 0 of 2", out)


class Cli(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = pc.main(list(argv))
        return rc, buf.getvalue()

    def test_index(self):
        d = tracked_mkdtemp()
        write(d, "pipeline.json", '{"context": {"index": ["r*"]}}')
        os.makedirs(f"{d}/r1/.git")
        with mock.patch.object(pc, "lp", lambda *a, **k: ""):
            rc, out = self.run_cli("index", "--workspace", d)
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"^index .*/r1: \d+ s\n$")

    def test_goal_and_stage_write_file(self):
        d = tracked_mkdtemp()
        write(d, "goal.md", "do the thing\n")
        out_file = os.path.join(d, "o", "ctx.md")
        with mock.patch.object(pc, "goal_context", lambda repo, text, cfg, log=None: f"G {repo} {text.strip()}\n"):
            rc, out = self.run_cli("goal", d, "--text-file", f"{d}/goal.md", "--out", out_file, "--workspace", d)
        self.assertEqual((rc, out), (0, f"plan_context goal: 1 lines -> {out_file}\n"))
        self.assertEqual(Path(out_file).read_text(encoding="utf-8"), f"G {d} do the thing\n")
        with mock.patch.object(pc, "stage_context", lambda repo, sd, b, h, cfg: "S\nT\n"):
            rc, out = self.run_cli("stage", d, "--stage-dir", d, "--base", "a", "--head", "b", "--out", out_file, "--verdict")
        self.assertEqual(out, f"VERDICT: DONE\nplan_context stage: 2 lines -> {out_file}\n")


if __name__ == "__main__":
    unittest.main()
