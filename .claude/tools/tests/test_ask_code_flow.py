"""Characterization tests for ask_code: index refresh, embedding, retrieval, ask and main, on a synthetic repo.
No model, no fastembed, no numpy: the embedder and numpy are small stubs."""

import contextlib
import fcntl
import io
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import askmodel
from search import ask_code

PY_SRC = (
    "# compute the daily cap for new items\n"
    "def daily_cap(user):\n"
    "    limit = 20\n"
    "    return min(limit, user.quota)\n"
    "\n"
    "\n"
    "def refreshQueue(items):\n"
    "    return [i for i in items if i]\n"
    "\n"
    "class Billing:\n"
    "    def charge(self):\n"
    "        return 1\n"
)

NO_ANSWER = {"answer": [], "idk": True, "closest": [], "queries": [], "reindexed": 0}


class _Vec:
    def __init__(self, xs):
        self.xs = list(xs)

    def tobytes(self):
        return struct.pack(f"{len(self.xs)}f", *self.xs)

    def reshape(self, n, _):
        k = len(self.xs) // n
        return _Mat([self.xs[i * k : (i + 1) * k] for i in range(n)])

    def __neg__(self):
        return _Vec([-x for x in self.xs])

    def __getitem__(self, s):
        return self.xs[s]


class _Mat:
    def __init__(self, rows):
        self.rows = rows

    def __matmul__(self, q):
        return _Vec([sum(a * b for a, b in zip(r, q.xs, strict=False)) for r in self.rows])


def _fake_numpy():
    np = types.ModuleType("numpy")
    np.asarray = lambda v, dtype=None: _Vec(v.xs if isinstance(v, _Vec) else v)  # type: ignore[attr-defined]  # noqa: V107 - mimics numpy.asarray(dtype=)
    np.frombuffer = lambda b, dtype=None: _Vec(struct.unpack(f"{len(b) // 4}f", b))  # type: ignore[attr-defined]  # noqa: V107 - mimics numpy.frombuffer(dtype=)
    np.argsort = lambda v: sorted(range(len(v.xs)), key=lambda i: v.xs[i])  # type: ignore[attr-defined]
    return np


class _Emb:
    """Stub fastembed model: deterministic 3-dim vectors."""

    def __init__(self):
        self.calls = 0

    def embed(self, texts, batch_size=64):  # noqa: V107 - mimics fastembed embed(batch_size=)
        for t in texts:
            self.calls += 1
            yield [float(len(t) % 7), float(t.count("daily")), 1.0]

    def query_embed(self, qs):
        for _ in qs:
            yield [0.0, 1.0, 0.0]


CTAGS = "\n".join(
    [
        "not json",
        json.dumps({"name": "daily_cap", "path": "{root}/app.py", "line": 2, "kind": "function"}),
        json.dumps({"name": "refreshQueue", "path": "{root}/app.py", "line": 7, "kind": "function"}),
        json.dumps({"name": "Billing", "path": "{root}/app.py", "line": 10, "kind": "class"}),
        json.dumps({"name": "limit", "path": "{root}/app.py", "line": 3, "kind": "variable"}),
        json.dumps({"name": "AnonymousFunction123", "path": "{root}/app.py", "line": 8, "kind": "function"}),
    ]
)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        Path(self.root, "app.py").write_text(PY_SRC, encoding="utf-8")
        Path(self.root, "notes.md").write_text("# Notes\nthe daily cap lives in the app module\n", encoding="utf-8")
        os.makedirs(os.path.join(self.root, "node_modules"))
        Path(self.root, "node_modules", "skip.js").write_text("function skipped() {}\n", encoding="utf-8")
        cache = os.path.join(self.root, "cache", "vectors.db")
        p = mock.patch.dict(os.environ, {"CODE_INDEX_CACHE": cache, "ASK_CODE_EMBED_BUDGET": "400"})
        p.start()
        self.addCleanup(p.stop)
        saved = dict(ask_code._emb)
        ask_code._emb.clear()
        ask_code._emb["m"] = None
        self.addCleanup(lambda: (ask_code._emb.clear(), ask_code._emb.update(saved)))
        ask_code.LAST.clear()
        self.ctags_calls = []
        real_run = ask_code.subprocess.run
        r = mock.patch.object(ask_code.subprocess, "run", side_effect=lambda cmd, **kw: self._ctags(cmd, real_run, **kw))
        r.start()
        self.addCleanup(r.stop)

    def _ctags(self, cmd, real_run, **kw):
        if cmd[0] != "ctags":  # other callers (git branch lookup) get the real subprocess
            return real_run(cmd, **kw)
        self.ctags_calls.append(kw.get("input", ""))
        return types.SimpleNamespace(stdout=CTAGS.replace("{root}", self.root))

    def use_embedder(self):
        emb = _Emb()
        ask_code._emb["m"] = emb
        p = mock.patch.dict(sys.modules, {"numpy": _fake_numpy()})
        p.start()
        self.addCleanup(p.stop)
        return emb

    def count(self, table):
        con = ask_code.connect(self.root)
        try:
            return con.execute(f"select count(*) from {table}").fetchone()[0]
        finally:
            con.close()


class RefreshTests(Base):
    def test_first_refresh_indexes_and_skips_junk(self):
        self.assertEqual(ask_code.refresh(self.root), (2, 0))
        con = ask_code.connect(self.root)
        files = sorted(r[0] for r in con.execute("select path from files"))
        self.assertEqual(files, ["app.py", "notes.md"])
        syms = {r[0] for r in con.execute("select name from symbols")}
        self.assertEqual(syms, {"daily_cap", "refreshQueue", "Billing", "limit"})  # Anonymous dropped
        self.assertEqual(self.count("spans"), 2)  # one chunk per file: slivers join
        self.assertEqual(self.count("chunks"), self.count("spans"))
        self.assertEqual(len(self.ctags_calls), 1)
        self.assertEqual(self.ctags_calls[0], os.path.join(self.root, "app.py"))  # only code files go to ctags
        self.assertEqual(ask_code.LAST["pending"], 0)  # no embedder: nothing pending
        self.assertTrue(os.path.exists(os.path.join(self.root, ".code-index", ".gitignore")))

    def test_second_refresh_is_a_noop(self):
        ask_code.refresh(self.root)
        self.assertEqual(ask_code.refresh(self.root), (0, 0))
        self.assertEqual(len(self.ctags_calls), 1)

    def test_touch_same_content_does_not_reparse(self):
        ask_code.refresh(self.root)
        os.utime(os.path.join(self.root, "app.py"), (1_000_000_000, 1_000_000_000))
        self.assertEqual(ask_code.refresh(self.root), (0, 0))
        con = ask_code.connect(self.root)
        self.assertEqual(con.execute("select mtime from files where path='app.py'").fetchone()[0], 1_000_000_000)
        self.assertEqual(len(self.ctags_calls), 1)

    def test_changed_and_deleted(self):
        ask_code.refresh(self.root)
        Path(self.root, "app.py").write_text(PY_SRC + "\ndef extra_thing():\n    return 2\n\n\n", encoding="utf-8")
        os.remove(os.path.join(self.root, "notes.md"))
        self.assertEqual(ask_code.refresh(self.root), (1, 1))
        con = ask_code.connect(self.root)
        self.assertEqual([r[0] for r in con.execute("select path from files")], ["app.py"])
        self.assertEqual(con.execute("select count(*) from spans where path='notes.md'").fetchone()[0], 0)

    def test_big_file_skipped(self):
        Path(self.root, "huge.md").write_text("x\n" * 600_000, encoding="utf-8")
        self.assertEqual(ask_code.refresh(self.root), (2, 0))
        con = ask_code.connect(self.root)
        self.assertNotIn("huge.md", [r[0] for r in con.execute("select path from files")])

    @unittest.skipIf(os.geteuid() == 0, "root reads anything")
    def test_unreadable_file_skipped(self):
        p = Path(self.root, "secret.md")
        p.write_text("hidden\n", encoding="utf-8")
        p.chmod(0)
        self.addCleanup(p.chmod, 0o600)
        self.assertEqual(ask_code.refresh(self.root), (2, 0))

    def test_fresh_rebuilds(self):
        ask_code.refresh(self.root)
        self.assertEqual(ask_code.refresh(self.root, fresh=True), (2, 0))

    def test_no_wait_busy_returns_zero(self):
        os.makedirs(os.path.join(self.root, ".code-index"))
        with open(os.path.join(self.root, ".code-index", "lock"), "w", encoding="utf-8") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            with mock.patch.object(ask_code.time, "sleep"):
                self.assertEqual(ask_code.refresh(self.root, wait=False), (0, 0))
        self.assertTrue(ask_code.LAST["busy"])
        self.assertEqual(ask_code.LAST["pending"], 0)

    def test_embed_zero_skips_embedding(self):
        self.use_embedder()
        ask_code.refresh(self.root, embed=0)
        self.assertEqual(ask_code.LAST["pending"], 0)
        self.assertEqual(self.count("vecs"), 0)

    def test_embed_budget_leaves_pending(self):
        emb = self.use_embedder()
        ask_code.refresh(self.root, embed=1)
        self.assertEqual(emb.calls, 1)
        self.assertEqual(ask_code.LAST["pending"], self.count("spans") - 1)

    def test_schema_mismatch_rebuilds(self):
        ask_code.refresh(self.root)
        con = sqlite3.connect(os.path.join(self.root, ".code-index", "index.db"))
        con.execute("pragma user_version=1")
        con.commit()
        con.close()
        self.assertEqual(ask_code.refresh(self.root), (2, 0))


class EmbedTests(Base):
    def test_no_embedder(self):
        ask_code.refresh(self.root)
        con = ask_code.connect(self.root)
        self.assertEqual(ask_code.embed_pending(con, self.root), (0, 0))

    def test_nothing_pending(self):
        self.use_embedder()
        ask_code.refresh(self.root)
        con = ask_code.connect(self.root)
        self.assertEqual(ask_code.embed_pending(con, self.root), (0, 0))

    def test_embeds_all_and_fills_cache_then_reuses_it(self):
        emb = self.use_embedder()
        ask_code.refresh(self.root, embed=0)
        con = ask_code.connect(self.root)
        n = con.execute("select count(*) from spans").fetchone()[0]
        self.assertEqual(ask_code.embed_pending(con, self.root), (n, 0))
        self.assertEqual(emb.calls, n)
        self.assertEqual(con.execute("select count(*) from vecs").fetchone()[0], n)
        # same text again, vectors dropped: all come from the shared cache
        con.execute("delete from vecs")
        con.commit()
        self.assertEqual(ask_code.embed_pending(con, self.root), (0, 0))
        self.assertEqual(emb.calls, n)
        self.assertEqual(con.execute("select count(*) from vecs").fetchone()[0], n)

    def test_budget(self):
        emb = self.use_embedder()
        ask_code.refresh(self.root, embed=0)
        con = ask_code.connect(self.root)
        n = con.execute("select count(*) from spans").fetchone()[0]
        self.assertEqual(ask_code.embed_pending(con, self.root, budget=2), (2, n - 2))
        self.assertEqual(emb.calls, 2)

    def test_unwritable_cache(self):
        self.use_embedder()
        ask_code.refresh(self.root, embed=0)
        con = ask_code.connect(self.root)
        n = con.execute("select count(*) from spans").fetchone()[0]
        with mock.patch.object(ask_code, "vcache", return_value=None):
            self.assertEqual(ask_code.embed_pending(con, self.root), (n, 0))

    def test_unreadable_file_gets_no_vector(self):
        emb = self.use_embedder()
        ask_code.refresh(self.root, embed=0)
        con = ask_code.connect(self.root)
        gone = con.execute("select count(*) from spans where path='notes.md'").fetchone()[0]
        total = con.execute("select count(*) from spans").fetchone()[0]
        self.assertGreater(gone, 0)
        os.remove(os.path.join(self.root, "notes.md"))
        self.assertEqual(ask_code.embed_pending(con, self.root), (total - gone, 0))
        self.assertEqual(emb.calls, total - gone)
        self.assertEqual(con.execute("select count(*) from vecs").fetchone()[0], total - gone)

    def test_vcache_unwritable_returns_none(self):
        with mock.patch.dict(os.environ, {"CODE_INDEX_CACHE": "/proc/nope/x/vectors.db"}):
            self.assertIsNone(ask_code.vcache())

    def test_cache_path_follows_workspace(self):
        ws = os.path.join(self.root, "ws")
        wt = os.path.join(ws, ".wt", "feat", "sub")
        os.makedirs(wt)
        Path(ws, "pipeline.json").write_text("{}", encoding="utf-8")
        with mock.patch.dict(os.environ):
            os.environ.pop("CODE_INDEX_CACHE")
            want = os.path.join(ws, ".code-index", "vectors.db")
            self.assertEqual(ask_code.cache_path(ws), want)
            self.assertEqual(ask_code.cache_path(wt), want)  # a worktree shares the project's cache
            self.assertEqual(ask_code.cache_path(self.root), os.path.join(ask_code.HOME, ".code-cache", "vectors.db"))  # no workspace above
            cc = ask_code.vcache(wt)
            self.assertIsNotNone(cc)
            cc.close()
            self.assertEqual(Path(ws, ".code-index", ".gitignore").read_text(encoding="utf-8"), "*\n")
        with mock.patch.dict(os.environ, {"CODE_INDEX_CACHE": "/x/v.db"}):
            self.assertEqual(ask_code.cache_path(ws), "/x/v.db")

    def test_embedder_without_library_is_none(self):
        ask_code._emb.clear()
        with mock.patch.dict(sys.modules, {"fastembed": None}):
            self.assertIsNone(ask_code.embedder())
        self.assertIsNone(ask_code.embedder())  # cached

    def test_embedder_with_library(self):
        ask_code._emb.clear()
        fake = types.ModuleType("fastembed")
        fake.TextEmbedding = lambda name, cache_dir=None: ("model", name, cache_dir)  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"fastembed": fake}):
            self.assertEqual(ask_code.embedder()[:2], ("model", ask_code.EMBED_MODEL))

    def test_vector_ranked(self):
        self.assertEqual(ask_code.vector_ranked(None, "q"), [])  # no embedder
        self.use_embedder()
        ask_code.refresh(self.root, embed=0)
        con = ask_code.connect(self.root)
        self.assertEqual(ask_code.vector_ranked(con, "q"), [])  # no vectors yet
        ask_code.embed_pending(con, self.root)
        self.assertEqual(len(ask_code.vector_ranked(con, "daily cap", n=2)), 2)


class RetrieveTests(Base):
    def setUp(self):
        super().setUp()
        Path(self.root, "tests").mkdir()
        Path(self.root, "tests", "test_app.py").write_text("def test_daily_cap():\n    assert daily_cap(1)\n", encoding="utf-8")
        ask_code.refresh(self.root)

    def test_symbol_hit_ranks_first(self):
        out = ask_code.retrieve(self.root, ["daily_cap"], [], "")
        self.assertEqual(out[0]["path"], "app.py")
        self.assertEqual(out[0]["score"], 100)
        self.assertEqual(out[0]["why"], "symbol function daily_cap")
        self.assertEqual(set(out[0]), {"path", "start", "end", "score", "why"})

    def test_partial_symbol_scores_40(self):
        out = ask_code.retrieve(self.root, ["daily"], [], "")
        top = [c for c in out if c["why"].startswith("symbol")]
        self.assertTrue(top)
        self.assertEqual(top[0]["score"], 40)

    def test_keywords_and_question_only(self):
        out = ask_code.retrieve(self.root, [], ["billing"], "who charges billing")
        self.assertEqual(out[0]["why"], "keyword")
        self.assertLessEqual(out[0]["score"], 20)
        self.assertIn("app.py", [c["path"] for c in out])

    def test_noise_ranks_below_source(self):
        paths = [c["path"] for c in ask_code.retrieve(self.root, [], ["daily", "cap"], "")]
        self.assertLess(paths.index("app.py"), paths.index("tests/test_app.py"))

    def test_limit_and_empty(self):
        self.assertEqual(len(ask_code.retrieve(self.root, [], ["daily"], "", limit=1)), 1)
        self.assertEqual(ask_code.retrieve(self.root, [], [], ""), [])

    def test_vector_fusion_adds_meaning_hits(self):
        self.use_embedder()
        con = ask_code.connect(self.root)
        ask_code.embed_pending(con, self.root)
        con.close()
        out = ask_code.retrieve(self.root, [], ["zzzunmatched"], "zzz", limit=10)
        self.assertTrue(out)
        self.assertTrue(all(c["why"] == "meaning" for c in out))
        out2 = ask_code.retrieve(self.root, ["daily_cap"], [], "daily cap", limit=10)
        self.assertEqual(out2[0]["why"], "symbol function daily_cap")

    def test_vocabulary_and_seen_names(self):
        names, stems = ask_code.vocabulary(self.root)
        self.assertIn("daily", names)
        self.assertIn("billing", names)
        self.assertNotIn("test", stems)
        self.assertIn("app", stems)
        out = ask_code.retrieve(self.root, ["daily_cap"], [], "")
        self.assertIn("daily_cap", ask_code.seen_names(self.root, out))


class AskTests(Base):
    def test_no_model(self):
        r = ask_code.ask(self.root, "daily cap", model=False)
        self.assertEqual(r["answer"][0]["path"], "app.py")
        self.assertFalse(r["idk"])
        self.assertEqual(r["reindexed"], 2)
        self.assertEqual(r["queries"], [{"symbols": [], "keywords": ["daily", "cap"]}])
        self.assertLessEqual(len(r["answer"]), 3)

    def test_no_model_idk(self):
        r = ask_code.ask(self.root, "zzzqqq", model=False)
        self.assertEqual((r["answer"], r["idk"], r["closest"]), ([], True, []))

    def test_model_hit_first_batch(self):
        with (
            mock.patch.object(askmodel, "translate", return_value=(["daily_cap"], ["quota"])) as tr,
            mock.patch.object(askmodel, "judge", side_effect=lambda root, q, part: [0]),
        ):
            r = ask_code.ask(self.root, "daily cap", model=True, rounds=2)
        tr.assert_called_once()
        self.assertFalse(r["idk"])
        self.assertEqual(len(r["answer"]), 1)
        self.assertEqual(r["closest"], [])
        self.assertEqual(r["queries"][0]["symbols"], ["daily_cap"])
        self.assertTrue(ask_code.LAST["all"])

    def test_model_hit_dropped_without_query_term(self):
        with (
            mock.patch.object(askmodel, "translate", return_value=(["zzz"], ["qqq"])),
            mock.patch.object(askmodel, "judge", side_effect=lambda root, q, part: list(range(len(part)))),
            mock.patch.object(askmodel, "snippet", return_value="nothing relevant"),
        ):
            r = ask_code.ask(self.root, "unrelated words", model=True, rounds=1)
        self.assertTrue(r["idk"])
        self.assertEqual(r["answer"], [])

    def test_second_round_uses_seen_names(self):
        calls = []

        def tr(q, vocab=None, seen=None):
            calls.append((q, vocab, seen))
            return (["daily_cap"], []) if len(calls) > 1 else (["nothing"], [])

        with (
            mock.patch.object(askmodel, "translate", side_effect=tr),
            mock.patch.object(askmodel, "judge", side_effect=lambda root, q, part: [0] if len(calls) > 1 else []),
        ):
            r = ask_code.ask(self.root, "daily cap", model=True, rounds=2, batch=1, depth=3)
        self.assertEqual(len(calls), 2)
        self.assertIn("first attempt found nothing", calls[1][0])
        self.assertIsNotNone(calls[1][1])
        self.assertEqual(len(r["queries"]), 2)

    def test_model_idk_returns_closest(self):
        with (
            mock.patch.object(askmodel, "translate", return_value=(["daily_cap"], [])),
            mock.patch.object(askmodel, "judge", return_value=[]),
        ):
            r = ask_code.ask(self.root, "daily cap", model=True, rounds=1)
        self.assertTrue(r["idk"])
        self.assertTrue(r["closest"])
        self.assertLessEqual(len(r["closest"]), 3)


class MainTests(Base):
    def run_main(self, args, **env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ask_code.main(args)
        return rc, out.getvalue(), err.getvalue()

    def test_help_exits_with_doc(self):
        for a in ([], ["-h"], ["--help"]):
            with self.assertRaises(SystemExit) as cm:
                ask_code.main(a)
            self.assertEqual(cm.exception.code, ask_code.__doc__)

    def test_plain_answer(self):
        rc, out, err = self.run_main(["daily cap", "--root", self.root, "--no-model"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("app.py:"), out)
        self.assertEqual(err, "")

    def test_json(self):
        rc, out, _ = self.run_main(["--root", self.root, "daily cap", "--no-model", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["answer"][0]["path"], "app.py")

    def test_idk(self):
        rc, out, _ = self.run_main(["zzzqqq", "--root", self.root, "--no-model"])
        self.assertEqual((rc, out.splitlines()[0]), (1, "IDK: nothing in the index answers this with confidence."))

    def test_idk_lists_closest(self):
        fake = {**NO_ANSWER, "closest": [{"path": "a.py", "start": 1, "end": 5, "why": "keyword"}]}
        with mock.patch.object(ask_code, "ask", return_value=fake):
            rc, out, _ = self.run_main(["q", "--root", self.root, "--no-model"])
        self.assertEqual(rc, 1)
        self.assertIn("  closest (unverified): a.py:1-5  keyword", out)

    def test_index_mode(self):
        rc, out, _ = self.run_main(["--index", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"^indexed \d+ chunks, 0 embedded, in \d+s\n$")
        rc, _, _ = self.run_main(["--index", "--reindex", "--root", self.root])
        self.assertEqual(rc, 0)

    def test_reindex_then_query(self):
        rc, _, _ = self.run_main(["daily", "--root", self.root, "--no-model", "--reindex"])
        self.assertEqual(rc, 0)

    def test_careful_sets_judge_think_and_env_think_is_per_call(self):
        with mock.patch.object(askmodel, "strata_up", return_value=True), mock.patch.object(ask_code, "ask", return_value=NO_ANSWER):
            self.run_main(["x", "--root", self.root, "--careful"], ASK_CODE_THINK="translate")
            self.assertEqual(askmodel.THINK, {"translate", "judge"})
            self.run_main(["x", "--root", self.root], ASK_CODE_THINK="")
            self.assertEqual(askmodel.THINK, set())

    def test_no_model_never_loads_the_client(self):
        with mock.patch.dict(sys.modules, {"localmodel.askmodel": None}):
            rc, out, _ = self.run_main(["daily cap", "--root", self.root, "--no-model"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("app.py:"))

    def test_no_root_uses_find_root(self):
        with mock.patch.object(ask_code, "find_root", return_value=self.root) as fr:
            rc, _, _ = self.run_main(["daily", "--no-model"])
        fr.assert_called_once()
        self.assertEqual(rc, 0)

    def test_no_model_reachable_falls_back(self):
        with (
            mock.patch.object(askmodel, "strata_up", return_value=False),
            mock.patch.object(ask_code.os.path, "exists", side_effect=lambda p: False if p == "/data/state" else os.path.lexists(p)),
        ):
            rc, _, err = self.run_main(["daily", "--root", self.root], STRATA_URL="")
        self.assertEqual(rc, 0)
        self.assertIn("(no local model reachable: keyword and symbol search only, results unverified)", err)

    def test_model_expected_waits(self):
        with (
            mock.patch.object(askmodel, "strata_up", return_value=False),
            mock.patch.object(askmodel, "wait_strata", return_value=True) as ws,
            mock.patch.object(ask_code, "ask", return_value=NO_ANSWER) as ak,
        ):
            rc, _, err = self.run_main(["daily", "--root", self.root], STRATA_URL="http://x", STRATA_WAIT_TRIES="5")
        ws.assert_called_once_with(5)
        self.assertTrue(ak.call_args.kwargs["model"])
        self.assertEqual((rc, err), (1, ""))

    def test_model_up_uses_model(self):
        with mock.patch.object(askmodel, "strata_up", return_value=True), mock.patch.object(ask_code, "ask", return_value=NO_ANSWER) as ak:
            self.run_main(["daily", "--root", self.root])
        self.assertTrue(ak.call_args.kwargs["model"])

    def test_busy_and_pending_notices(self):
        def fake_ask(*a, **k):
            ask_code.LAST["busy"] = True
            ask_code.LAST["pending"] = 7
            return {**NO_ANSWER, "idk": False, "answer": [{"path": "a.py", "start": 1, "end": 2, "why": "keyword"}]}

        with mock.patch.object(ask_code, "ask", side_effect=fake_ask):
            rc, out, err = self.run_main(["q", "--root", self.root, "--no-model"])
        self.assertEqual(out, "a.py:1-2  keyword\n")
        self.assertIn("being built right now by another process", err)
        self.assertIn("semantic index still building: 7 chunks not embedded yet", err)
        self.assertEqual(rc, 0)

    def test_question_skips_root_value(self):
        with mock.patch.object(ask_code, "ask", return_value=NO_ANSWER) as ak:
            self.run_main(["--root", self.root, "--no-model", "my question"])
        self.assertEqual(ak.call_args.args[1], "my question")
        with mock.patch.object(ask_code, "ask", return_value=NO_ANSWER) as ak:
            self.run_main(["--root", self.root, "--no-model"])
        self.assertEqual(ak.call_args.args[1], "")


class SmallHelperTests(Base):
    def test_find_root(self):
        sub = os.path.join(self.root, "a", "b")
        os.makedirs(sub)
        os.makedirs(os.path.join(self.root, ".code-index"))
        self.assertEqual(ask_code.find_root(sub), self.root)

    def test_embedder_reports_a_load_failure_but_not_a_missing_library(self):  # audit: broad except reports through swallowed
        fake = types.ModuleType("fastembed")
        fake.TextEmbedding = mock.Mock(side_effect=RuntimeError("model files unreadable"))  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"fastembed": fake}), mock.patch.dict(ask_code._emb, clear=True), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(ask_code.embedder())
        self.assertIn("swallowed in ask_code.embedder: RuntimeError", err.getvalue())
        with mock.patch.dict(sys.modules, {"fastembed": None}), mock.patch.dict(ask_code._emb, clear=True), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(ask_code.embedder())
        self.assertEqual(err.getvalue(), "")

    def test_words_and_spans(self):
        self.assertEqual(ask_code.words("refreshQueue the daily_cap"), ["refresh", "queue", "daily", "cap"])
        self.assertEqual(ask_code.spans_for(["x"] * 65, None), [(1, 30), (31, 60), (61, 65)])
        lines = ["# c", "def a():", "    pass", "", "", "", "", "", "def b():", "    pass"]
        self.assertEqual(ask_code.spans_for(lines, {2, 9}), [(1, 10)])  # 2-line tail joins the previous chunk

    def test_jsonobj_and_snippet(self):
        self.assertEqual(askmodel.jsonobj('noise {"a": 1} tail'), {"a": 1})
        self.assertEqual(askmodel.jsonobj("{bad"), {})
        self.assertEqual(askmodel.jsonobj("none"), {})
        Path(self.root, "f.txt").write_text("\n".join(f"l{i}" for i in range(1, 30)), encoding="utf-8")
        s = askmodel.snippet(self.root, {"path": "f.txt", "start": 2, "end": 3})
        self.assertEqual(s.splitlines()[0], "l2")
        self.assertEqual(len(s.splitlines()), 12)
        self.assertEqual(askmodel.snippet(self.root, {"path": "nope.txt", "start": 1, "end": 2}), "")


class BrokenFileTests(unittest.TestCase):
    def test_broken_symlink_is_skipped_by_scan(self):  # a dangling x.py must not crash the indexer
        with tempfile.TemporaryDirectory() as root:
            open(os.path.join(root, "ok.py"), "w").close()
            os.symlink(os.path.join(root, "gone"), os.path.join(root, "x.py"))
            now, _, _ = ask_code._scan_files(None, root, {})
        self.assertIn("ok.py", now)
        self.assertNotIn("x.py", now)

    def test_missing_ctags_degrades_to_no_symbols(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(ask_code.subprocess, "run", side_effect=FileNotFoundError("ctags")):
            self.assertEqual(ask_code._store_symbols(None, root, ["a.py"]), {})


if __name__ == "__main__":
    unittest.main()
