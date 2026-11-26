import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import plan_context as pc
from search import ctx_config as cc
from search import leadparts
from tests.support.tmpdirs import tracked_mkdtemp


def fake_lp(table):
    """lp stub: answers by the first key whose words are prefixes of the args (more specific keys first), else empty like a missing server."""

    def run(repo, *a, t=60):
        for k, v in table.items():
            if len(a) >= len(k) and all(a[i].startswith(k[i]) for i in range(len(k))):
                return v
        return ""

    return run


_NO_EXPLORE = mock.patch("localmodel.explore.explore", return_value=None)  # the explorer would call a real Strata


def setUpModule():
    _NO_EXPLORE.start()


def tearDownModule():
    _NO_EXPLORE.stop()


class GoalContext(unittest.TestCase):
    def setUp(self):
        self._lp, self._lp2, self._mf = pc.lp, leadparts.lp, pc.model_files
        pc.model_files = lambda *a: []
        self.repo = tracked_mkdtemp()
        os.makedirs(f"{self.repo}/src")
        open(f"{self.repo}/src/a.py", "w").close()

    def tearDown(self):
        pc.lp, leadparts.lp, pc.model_files = self._lp, self._lp2, self._mf

    def test_collects_find_symbols_outline_and_tests(self):
        pc.lp = leadparts.lp = fake_lp(
            {
                ("find", "tests for"): "tests/a_test.py:1-9  keyword\nsrc/a.py:1-2  keyword\n",
                ("find",): "src/a.py:10-20  symbol function make\n",
                ("nav", "symbols"): "src/a.py:10  function make\n",
                ("nav", "outline"): "10:1  function make\n",
            }
        )
        out = pc.goal_context(self.repo, "Change `make` so it rejects empty names")
        self.assertIn("src/a.py:10-20", out)
        self.assertIn("`make`: src/a.py:10", out)
        self.assertIn("src/a.py: make@10", out)
        self.assertIn("tests/a_test.py", out)
        self.assertNotIn("src/a.py:1-2", out.split("## tests")[-1])  # only test-looking paths in the tests section

    def test_fails_open_when_lp_answers_nothing(self):
        pc.lp = leadparts.lp = fake_lp({})
        out = pc.goal_context("/x", "anything")
        self.assertIn("(no answer)", out)
        self.assertNotIn("## symbols", out)


class StageContext(unittest.TestCase):
    def test_changed_files_outside_tasks_and_unused_paths(self):
        d = tracked_mkdtemp()

        def git(*a):
            return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)

        git("init", "-q", "-b", "master")
        Path(os.path.join(d, "a.py")).write_text("x\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "init")
        git("checkout", "-q", "-b", "dev")
        os.makedirs(os.path.join(d, "src"))
        Path(os.path.join(d, "a.py")).write_text("y\n", encoding="utf-8")
        Path(os.path.join(d, "src", "b.py")).write_text("z\n", encoding="utf-8")
        Path(os.path.join(d, "stray.txt")).write_text("s\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "work")
        sd = os.path.join(d, ".work", "s")
        os.makedirs(sd)
        Path(os.path.join(sd, "task-1.md")).write_text("# task-1: edit a\nPredicted paths: a.py, src/\nDone: a changed\n", encoding="utf-8")
        Path(os.path.join(sd, "task-2.md")).write_text("# task-2: docs\nPredicted paths: docs/\nDone: docs written\n", encoding="utf-8")
        out = pc.stage_context(d, sd, "master", "dev")
        self.assertIn("## task-1: edit a\n", out)
        self.assertIn("a.py, src/b.py", out)  # changed under task-1's paths
        outside = out.split("## Changed but not in any task's Predicted paths")[1].split("## Predicted paths with no change")[0]
        self.assertIn("stray.txt", outside)
        self.assertNotIn("a.py", outside)
        self.assertIn("- docs/", out.split("## Predicted paths with no change")[1])


class ModelFiles(unittest.TestCase):
    def setUp(self):
        from search import strata

        self.ls, self.st = strata.strata_call, strata.strata_up
        self.strata: Any = strata  # tests swap its functions for fakes
        self.repo = tracked_mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=False)
        for f in ("tools/a.py", "tools/b.py", ".cache/x.json"):
            os.makedirs(os.path.dirname(f"{self.repo}/{f}"), exist_ok=True)
            open(f"{self.repo}/{f}", "w").close()

    def tearDown(self):
        self.strata.strata_call, self.strata.strata_up = self.ls, self.st

    def test_names_per_section_drops_unknown_and_ignored_files(self):
        calls = []
        self.strata.strata_call = lambda p, max_tokens=0, thinking=True: calls.append(p) or '{"files": ["tools/a.py", "tools/ghost.py", ".cache/x.json"]}'
        self.strata.strata_up = lambda: True
        got = pc.model_files(self.repo, "first section has six words here\nsecond section has six words too", {**pc.CTX_DEFAULTS, "batch": False})
        self.assertEqual(got, ["tools/a.py"])
        self.assertEqual(len(calls), 2)
        self.assertNotIn(".cache/x.json", calls[0])

    def test_batch_is_one_call_ordered_and_fills_per(self):
        calls = []
        ans = '{"files": [{"path": "tools/a.py", "lines": [2]}, {"path": "tools/ghost.py", "lines": [1]}, {"path": "tools/b.py", "lines": [1, 2]}]}'
        self.strata.strata_call = lambda p, max_tokens=0, thinking=True: calls.append(p) or ans
        self.strata.strata_up = lambda: True
        per: list = []
        text = "first section has six words here\nsecond section has six words too"
        got = pc.model_files(self.repo, text, pc.CTX_DEFAULTS, per=per)
        self.assertEqual(got, ["tools/a.py", "tools/b.py"])
        self.assertEqual(len(calls), 1)
        self.assertEqual([p[1] for p in per], [["tools/b.py"], ["tools/a.py", "tools/b.py"]])
        pc.model_files(self.repo, text, pc.CTX_DEFAULTS)
        self.assertEqual(len(calls), 1)

    def test_bad_batch_answer_falls_back_to_per_section(self):
        calls = []
        self.strata.strata_call = lambda p, max_tokens=0, thinking=True: calls.append(p) or ("no json" if "REQUEST LINES" in p else '{"files": ["tools/a.py"]}')
        self.strata.strata_up = lambda: True
        got = pc.model_files(self.repo, "first section has six words here\nsecond section has six words too", pc.CTX_DEFAULTS)
        self.assertEqual((got, len(calls)), (["tools/a.py"], 3))

    def test_offline_or_disabled_gives_nothing(self):
        self.strata.strata_up = lambda: False
        self.assertEqual(pc.model_files(self.repo, "a b c d e f", {**pc.CTX_DEFAULTS, "batch": False}), [])
        self.strata.strata_up = lambda: True
        self.assertEqual(pc.model_files(self.repo, "a b c d e f", {**pc.CTX_DEFAULTS, "model": False}), [])

    def test_pick_root_uses_nested_repo_named_in_text(self):
        sub = f"{self.repo}/workspace/course"
        os.makedirs(sub)
        subprocess.run(["git", "init", "-q"], cwd=sub, check=False)
        self.assertEqual(pc.pick_root(self.repo, "see workspace/course/tools/x.py"), sub)
        self.assertEqual(pc.pick_root(self.repo, "see tools/a.py"), self.repo)

    def test_config_overlays_pipeline_json(self):
        Path(f"{self.repo}/pipeline.json").write_text('{"context": {"per_section": 5}}', encoding="utf-8")
        c = pc.ctx_config(self.repo)
        self.assertEqual((c["per_section"], c["model"]), (5, True))

    def test_digest_top_comment_and_doc_cite(self):
        Path(f"{self.repo}/tools/a.py").write_text('"""Does the A thing.\n\nMore."""\nx = 1\n', encoding="utf-8")
        Path(f"{self.repo}/CONTRACT.md").write_text("# C\nrule: a.py is append-only\n", encoding="utf-8")
        d = pc.digest(self.repo, ["tools/a.py", "tools/b.py"])
        self.assertEqual(d[1], "- tools/a.py - Does the A thing.\n    CONTRACT.md:2: rule: a.py is append-only")
        self.assertEqual(d[2], "- tools/b.py")
        self.assertEqual(pc.digest(self.repo, ["tools/test_a.py", "tools/test_b.py"]), ["### tests", "- tools/: test_a.py, test_b.py"])

    def test_ensure_index_runs_lp_index_in_each_named_git_repo(self):
        ws = tracked_mkdtemp()
        seen = []
        for d in ("a", "courses/c1", "courses/c2", "courses/plain"):
            os.makedirs(f"{ws}/{d}")
        for d in ("a", "courses/c1", "courses/c2"):
            os.mkdir(f"{ws}/{d}/.git")
        saved = cc.lp
        cc.lp = lambda repo, *a, t=60: seen.append((os.path.relpath(repo, ws), a)) or ""
        try:
            done = pc.ensure_index(ws, {**pc.CTX_DEFAULTS, "index": ["a", "courses/*"]})
        finally:
            cc.lp = saved
        self.assertEqual([r for r, _ in seen], ["a", "courses/c1", "courses/c2"])
        self.assertEqual(seen[0][1], ("index",))
        self.assertEqual(len(done), 3)
        self.assertEqual(pc.ensure_index(ws, pc.CTX_DEFAULTS), [])

    def test_ensure_index_announces_a_first_build_only(self):
        ws = tracked_mkdtemp()
        for d in ("new", "old"):
            os.makedirs(f"{ws}/{d}/.git")
        os.mkdir(f"{ws}/old/.code-index")
        with mock.patch.object(cc, "lp", lambda *a, **k: ""), mock.patch("sys.stderr") as err:
            pc.ensure_index(ws, {**pc.CTX_DEFAULTS, "index": ["new", "old"]})
        said = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("indexing", said)
        self.assertIn("/new", said)
        self.assertNotIn("/old", said)

    def test_code_files(self):
        ctx = "### code (most-named first)\n- tools/a.py - Does A.\n    CONTRACT.md:2: x\n- tools/b.py\n### tests\n- tools/: t.py\n"
        self.assertEqual(pc.code_files(ctx), ["tools/a.py", "tools/b.py"])

    def test_naming_is_cached_per_section_and_survives_a_new_run(self):
        calls = []
        self.strata.strata_call = lambda p, max_tokens=0, thinking=True: calls.append(p) or '{"files": ["tools/a.py"]}'
        self.strata.strata_up = lambda: True
        text = "first section has six words here\nsecond section has six words too"
        self.assertEqual(pc.model_files(self.repo, text, {**pc.CTX_DEFAULTS, "batch": False}), ["tools/a.py"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(pc.model_files(self.repo, text, {**pc.CTX_DEFAULTS, "batch": False}), ["tools/a.py"])
        self.assertEqual(len(calls), 2)  # both answers came from the cache
        pc.model_files(self.repo, text + "\nthird section has six words also", {**pc.CTX_DEFAULTS, "batch": False})
        self.assertEqual(len(calls), 3)  # only the new line asks the model
        open(f"{self.repo}/tools/c.py", "w").close()
        pc.model_files(self.repo, text, {**pc.CTX_DEFAULTS, "batch": False})
        self.assertEqual(len(calls), 5)  # a changed file list invalidates the entries

    def test_cache_key_changes_with_local_model_and_prompt(self):
        def k():
            return pc.naming_key("h", pc.CTX_DEFAULTS, "line")

        base = k()
        self.assertEqual(base, k())
        old = os.environ.get("STRATA_MODEL")
        os.environ["STRATA_MODEL"] = "other-model"
        try:
            self.assertNotEqual(base, k())
        finally:
            os.environ.pop("STRATA_MODEL") if old is None else os.environ.__setitem__("STRATA_MODEL", old)
        saved = pc.NAMING_ASK
        pc.NAMING_ASK = saved + " more"
        try:
            self.assertNotEqual(base, k())
        finally:
            pc.NAMING_ASK = saved
        self.assertNotEqual(base, pc.naming_key("h", {**pc.CTX_DEFAULTS, "per_section": 3}, "line"))

    def test_context_paths_live_recall_and_stage_event(self):
        ctx = (
            "## files the local model names\n### code (most-named first)\n- tools/a.py - Does A.\n    CONTRACT.md:2: x\n- tools/b.py\n### tests\n- tools/: t.py, u.py\n"
            "### docs\n- CONTRACT.md - C\n## brief\n- prose line [tools/zzz.py]\n"
        )
        self.assertEqual(pc.context_paths(ctx), ["tools/a.py", "tools/b.py", "tools/t.py", "tools/u.py", "CONTRACT.md"])

        def git(*a):
            return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=self.repo, capture_output=True, text=True, check=True)

        for f in ("tools/a.py", "tools/b.py", "CONTRACT.md"):
            Path(f"{self.repo}/{f}").write_text("x\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD").stdout.strip()
        for f in ("tools/a.py", "tools/b.py"):
            with open(f"{self.repo}/{f}", "a", encoding="utf-8") as fh:
                fh.write("y\n")
        Path(f"{self.repo}/tools/new.py").write_text("n\n", encoding="utf-8")
        Path(f"{self.repo}/README.md").write_text("r\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "work")
        head = git("rev-parse", "HEAD").stdout.strip()
        with open(f"{self.repo}/README.md", "a", encoding="utf-8") as fh:
            fh.write("z\n")
        git("add", "-A")
        git("commit", "-qm", "more")
        head = git("rev-parse", "HEAD").stdout.strip()
        Path(f"{self.repo}/tools/pack-01.mp3").write_text("m\n", encoding="utf-8")
        Path(f"{self.repo}/tools/x.py").write_text("m\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "pack")
        base = git("rev-parse", "HEAD").stdout.strip()
        with open(f"{self.repo}/tools/pack-01.mp3", "a", encoding="utf-8") as fh:
            fh.write("m2\n")
        with open(f"{self.repo}/tools/a.py", "a", encoding="utf-8") as fh:
            fh.write("again\n")
        git("add", "-A")
        git("commit", "-qm", "again")
        head = git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(pc.live_recall(self.repo, base, head, ctx), (1, 1, []))  # the ignored .mp3 does not count against recall
        base = git("rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
        ctx2 = ctx.replace("- tools/b.py\n", "")
        self.assertEqual(pc.live_recall(self.repo, base, head, ctx2), (1, 2, ["tools/b.py"]))  # new.py and README.md are added files: not counted
        stage = tracked_mkdtemp()
        Path(f"{stage}/plan-context.md").write_text(ctx2, encoding="utf-8")
        out = pc.stage_context(self.repo, stage, base, head)
        self.assertIn("Planner context recall: 1 of 2", out)
        import json

        with open(f"{stage}/metrics.jsonl", encoding="utf-8") as fh:
            ev = [json.loads(line) for line in fh]
        self.assertEqual((ev[0]["ev"], ev[0]["hits"], ev[0]["total"], ev[0]["missed"]), ("context-recall", 1, 2, ["tools/b.py"]))
        self.assertEqual(pc.find_context_file(tracked_mkdtemp()), "")


class SectionVotesBudgetTest(unittest.TestCase):
    def test_sections_after_the_budget_are_skipped(self):
        asked = []
        clock = iter([0, 0, pc.FALLBACK_BUDGET_S + 1, pc.FALLBACK_BUDGET_S + 1])

        def name(strata, fs, cfg, part):
            asked.append(part)
            return ["a.py"]

        with mock.patch.object(pc, "_name_section", name), mock.patch.object(pc.time, "time", lambda: next(clock)):
            votes = pc._section_votes(None, ["a.py"], {"per_section": 1}, ["one", "two"], "k", {}, None)
        self.assertEqual((asked, votes), (["one"], {"a.py": 1}))


if __name__ == "__main__":
    unittest.main()
