"""lsp characterization tests that need no real language server.

Covers argument handling, the NOT CHECKED fallbacks, JS/syntax checks, result formatting, and the client/server protocol
against a tiny fake stdio server (FAKE_SERVER below). Fake `node`, `npm`, `rg` and `ctags` scripts go on a temp PATH.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import lsp
from tests.support.tmpdirs import tracked_mkdtemp

# Minimal LSP server: answers a fixed set of requests; BAD in a file's text yields an error diagnostic, PULL a pulled warning.
FAKE_SERVER = r"""
import json, os, sys

def read():
    n = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length:"):
            n = int(line.split(b":")[1])
    return json.loads(sys.stdin.buffer.read(n))

def send(m):
    b = json.dumps(m).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(b) + b)
    sys.stdout.buffer.flush()

def rng(line, ch):
    return {"start": {"line": line, "character": ch}, "end": {"line": line, "character": ch + 1}}

texts = {}
outline_seen = 0
while True:
    m = read()
    method, rid, p = m.get("method"), m.get("id"), m.get("params")
    if method == "initialize" and os.environ.get("FAKE_INIT_ERROR") == "1":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "init refused"}})
    elif method == "initialize":
        send({"jsonrpc": "2.0", "id": 900, "method": "workspace/configuration", "params": {"items": [{"section": "python"}, {"section": "python.analysis"}, {"section": "x"}]}})
        send({"jsonrpc": "2.0", "id": rid, "result": {"capabilities": {}}})
    elif method == "textDocument/didOpen":
        d = p["textDocument"]
        texts[d["uri"]] = d["text"]
        diags = []
        if "BAD" in d["text"]:
            diags = [{"range": rng(2, 3), "severity": 1, "message": "bad thing\nsecond line"}, {"range": rng(0, 0), "severity": 2, "message": "prefix line warning"}]
        if os.environ.get("FAKE_SILENT") != "1":
            send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": d["uri"], "diagnostics": diags}})
    elif method == "textDocument/diagnostic":
        u = p["textDocument"]["uri"]
        if "PULL" in texts.get(u, ""):
            send({"jsonrpc": "2.0", "id": rid, "result": {"kind": "full", "items": [{"range": rng(0, 1), "severity": 2, "message": "pulled"}]}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unsupported"}})
    elif method == "textDocument/definition":
        u = p["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "id": rid, "result": [{"uri": u, "range": rng(0, 4)}, {"targetUri": u, "targetRange": rng(1, 0), "targetSelectionRange": rng(1, 2)}]})
    elif method == "textDocument/references":
        u = p["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "id": rid, "result": [{"uri": u, "range": rng(0, 4)}, {"uri": u, "range": rng(1, 0)}]})
    elif method == "textDocument/hover":
        mode = os.environ.get("FAKE_HOVER", "markup")
        res = {"markup": {"contents": {"kind": "markdown", "value": "  int  \n"}},
               "list": {"contents": ["a", {"value": "b"}]},
               "str": {"contents": "plain"},
               "none": None}[mode]
        send({"jsonrpc": "2.0", "id": rid, "result": res})
    elif method == "textDocument/documentSymbol":
        outline_seen += 1  # FAKE_OUTLINE_EMPTY=N: the first N answers are [] (project still loading); "all": always []
        empty = os.environ.get("FAKE_OUTLINE_EMPTY", "0")
        if empty == "all" or outline_seen <= int(empty):
            send({"jsonrpc": "2.0", "id": rid, "result": []})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": [{"name": "foo", "kind": 12, "selectionRange": rng(0, 4)}, {"name": "Bar", "kind": 5, "location": {"uri": "x", "range": rng(3, 0)}}, {"name": "odd", "kind": 99, "selectionRange": rng(5, 1)}]})
    elif method == "workspace/symbol":
        send({"jsonrpc": "2.0", "id": rid, "result": []})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": rid, "result": None})
    elif method == "exit":
        sys.exit(0)
    elif rid is not None and method:
        send({"jsonrpc": "2.0", "id": rid, "result": None})
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tracked_mkdtemp())
        self.bin = os.path.join(self.d, "bin")
        os.mkdir(self.bin)
        self.proj = os.path.join(self.d, "proj")
        os.mkdir(self.proj)
        Path(self.proj, "pyproject.toml").write_text("", encoding="utf-8")
        Path(self.d, "fake_server.py").write_text(FAKE_SERVER, encoding="utf-8")
        self.fake_cmd = [sys.executable, os.path.join(self.d, "fake_server.py")]
        patches = [
            mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]}),
            mock.patch.dict(lsp.SERVERS, {k: (self.fake_cmd, v[1]) for k, v in lsp.SERVERS.items()}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        cwd = os.getcwd()
        os.chdir(self.proj)
        self.addCleanup(os.chdir, cwd)

    def write(self, name, text):
        p = os.path.join(self.proj, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Path(p).write_text(text, encoding="utf-8")
        return p

    def script(self, name, body):
        p = Path(self.bin, name)
        p.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        p.chmod(0o755)

    def run_main(self, *args):
        """Return (rc or SystemExit message, stdout lines)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = lsp.main(list(args))
            except SystemExit as e:
                rc = e.code
        return rc, buf.getvalue().splitlines()


class ArgumentTests(Base):
    def test_usage_on_bad_op_or_too_few_args(self):
        self.assertEqual(self.run_main("bogus", "x")[0], lsp.__doc__)
        self.assertEqual(self.run_main("def")[0], lsp.__doc__)
        self.assertEqual(self.run_main()[0], lsp.__doc__)

    def test_missing_file(self):
        self.assertTrue(str(self.run_main("def", "nope.py", "1")[0]).startswith("nope.py: no such file"))
        self.assertTrue(str(self.run_main("diag", "nope.py")[0]).startswith("nope.py: no such file"))

    def test_unknown_type(self):
        self.write("a.zzz", "hi\n")
        self.assertEqual(self.run_main("def", "a.zzz", "1"), (0, ["no language server for a.zzz: use Grep or Read for this file type"]))
        self.assertEqual(self.run_main("outline", "a.zzz"), (0, ["no language server for a.zzz: use Grep or Read for this file type"]))
        self.assertEqual(self.run_main("diag", "a.zzz"), (3, ["NOT CHECKED: no language server for a.zzz"]))

    def test_server_missing_non_diag(self):
        self.write("a.py", "x = 1\n")
        lsp.SERVERS["py"] = (["no-such-server-binary"], "python")
        rc, out = self.run_main("def", "a.py", "1")
        self.assertEqual(rc, 3)
        self.assertTrue(out[0].startswith("server failed to start: "))

    def test_diag_server_missing_falls_back(self):
        self.write("ok.py", "x = 1\n")
        self.write("bad.py", "def f(:\n")
        lsp.SERVERS["py"] = (["no-such-server-binary"], "python")
        rc, out = self.run_main("diag", "ok.py")
        self.assertEqual((rc, out[0].startswith("syntax OK only (server failed to start: ")), (3, True))
        self.assertTrue(out[0].endswith("type/semantic errors NOT CHECKED"))
        rc, out = self.run_main("diag", "bad.py")
        self.assertEqual(rc, 1)
        self.assertIn("bad.py  syntax error (py_compile; server failed to start", out[0])

    def test_symbol_not_found(self):
        self.write("a.py", "x = 1\n")
        rc, _ = self.run_main("def", "a.py", "missing")
        self.assertEqual(rc, "symbol 'missing' not found in a.py; no declarations found in the file")


class JsDiagTests(Base):
    """JS/TS diag goes through the server (tsc --lsp); node --check is only the fallback of not_checked."""

    def test_js_ts_jsx_diag_use_the_server_not_node(self):
        self.script("node", "echo called; exit 1\n")
        for name in ("a.js", "a.jsx", "a.ts"):
            self.write(name, "1\n")
            rc, out = self.run_main("diag", name)
            self.assertEqual(rc, 0, name)
            self.assertNotIn("called", "\n".join(out))

    def test_js_diag_without_server_is_not_checked(self):
        self.write("a.js", "1\n")
        self.script("node", "exit 0\n")
        with mock.patch.dict(lsp.SERVERS, {"js": (["/nonexistent/tsc"], "javascript")}):
            rc, out = self.run_main("diag", "a.js")
        self.assertEqual(rc, 3)
        self.assertIn("syntax OK only", out[0])

    def test_not_checked_helper_variants(self):
        f = self.write("a.js", "x\n")
        self.script("node", "echo 'boom' >&2; exit 2\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(lsp.not_checked(f, "js", "why"), 1)
            self.assertEqual(lsp.not_checked(f, "cpp", "no tool"), 3)
        self.assertIn("syntax error (node --check; why; type errors NOT CHECKED): boom", buf.getvalue())
        self.assertIn("NOT CHECKED: no tool", buf.getvalue())
        self.script("node", "exit 0\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(lsp.not_checked(f, "js", "why"), 3)
        self.assertEqual(buf.getvalue().strip(), "syntax OK only (why); type/semantic errors NOT CHECKED")

    def test_not_checked_without_node_binary(self):
        f = self.write("a.js", "x\n")
        with mock.patch.object(lsp.subprocess, "run", side_effect=FileNotFoundError):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(lsp.not_checked(f, "js", "w"), 3)
        self.assertEqual(buf.getvalue().strip(), "NOT CHECKED: w")


class ServerProtocolTests(Base):
    def test_def(self):
        self.write("a.py", "def foo():\n    return 1\n")
        rc, out = self.run_main("def", "a.py", "foo")
        self.assertEqual(rc, 0)
        self.assertEqual(out, ["a.py:1:5  def foo():", "a.py:2:3  return 1"])

    def test_def_by_line_col(self):
        self.write("a.py", "def foo():\n    return 1\n")
        self.assertEqual(self.run_main("def", "a.py", "1:5")[0], 0)

    def test_refs_py(self):
        self.write("a.py", "def foo():\n    return 1\n")
        rc, out = self.run_main("refs", "a.py", "1")
        self.assertEqual(rc, 0)
        self.assertEqual(out, ["a.py:1:5  def foo():", "a.py:2:1  return 1"])

    def test_refs_js_adds_text_hits(self):
        self.write("a.js", "function foo() {}\nfoo();\n")
        self.write("b.js", "const {foo} = require('./a');\n")
        self.script("npm", "echo /nowhere\n")
        a_abs = os.path.join(self.proj, "a.js")
        b_abs = os.path.join(self.proj, "b.js")
        self.script("rg", f"echo '{a_abs}:2:1:foo();'\necho '{b_abs}:1:8:const {{foo}}'\n")
        rc, out = self.run_main("refs", "a.js", "foo")
        self.assertEqual(rc, 0)
        self.assertEqual(out[:2], ["a.js:1:5  function foo() {}", "a.js:2:1  foo();"])
        self.assertIn("# 1 more word-match hits (text search, not verified as the same symbol):", out)
        self.assertEqual(out[-1], "b.js:1:8  const {foo} = require('./a');")

    def test_hover_variants(self):
        self.write("a.py", "x = 1\n")
        for mode, want in (("markup", "int"), ("list", "a\nb"), ("str", "plain"), ("none", "(none)")):
            with mock.patch.dict(os.environ, {"FAKE_HOVER": mode}):
                rc, out = self.run_main("hover", "a.py", "1")
            self.assertEqual((rc, "\n".join(out)), (0, want))

    def test_outline(self):
        self.write("a.py", "x = 1\n")
        rc, out = self.run_main("outline", "a.py")
        self.assertEqual((rc, out), (0, ["1:5  function foo", "4:1  class Bar", "6:2  sym odd"]))

    def test_outline_retries_empty_answers_while_the_project_loads(self):
        self.write("a.py", "x = 1\n")
        clock = [0.0]  # mock clock: the retry sleeps advance it, no wall-clock wait
        with (
            mock.patch.dict(os.environ, {"FAKE_OUTLINE_EMPTY": "2"}),
            mock.patch.object(lsp.time, "time", side_effect=lambda: clock[0]),
            mock.patch.object(lsp.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)),
        ):
            rc, out = self.run_main("outline", "a.py")
        self.assertEqual((rc, out), (0, ["1:5  function foo", "4:1  class Bar", "6:2  sym odd"]))
        self.assertLessEqual(clock[0], 2.5)  # _open_for gives a .py file no extra wait

    def test_outline_of_an_empty_file_is_rc_0_after_the_bounded_wait(self):
        self.write("a.py", "")
        clock = [0.0]
        with (
            mock.patch.dict(os.environ, {"FAKE_OUTLINE_EMPTY": "all"}),
            mock.patch.object(lsp.time, "time", side_effect=lambda: clock[0]),
            mock.patch.object(lsp.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)),
        ):
            rc, out = self.run_main("outline", "a.py")
        self.assertEqual((rc, out), (0, []))
        self.assertAlmostEqual(clock[0], 2.5)  # waited the whole budget, then gave up

    def test_diag_published(self):
        self.write("a.py", "x = 1\nBAD\n")
        rc, out = self.run_main("diag", "a.py")
        self.assertEqual(rc, 0)
        self.assertEqual(out, ["a.py:1:1  warning: prefix line warning", "a.py:3:4  error: bad thing"])

    def test_diag_clean(self):
        self.write("a.py", "x = 1\n")
        self.assertEqual(self.run_main("diag", "a.py"), (0, ["(no diagnostics)"]))

    def test_diag_pull_model(self):
        self.write("a.py", "# PULL\n")
        rc, out = self.run_main("diag", "a.py")
        self.assertEqual(rc, 0)
        self.assertEqual(out, ["a.py:1:2  warning: pulled"])

    def test_diag_silent_server_is_not_checked(self):
        self.write("a.py", "x = 1\n")
        with mock.patch.dict(os.environ, {"FAKE_SILENT": "1"}), mock.patch.object(lsp.Client, "settle", return_value=False):
            rc, out = self.run_main("diag", "a.py")
        self.assertEqual(rc, 3)
        self.assertTrue(out[0].startswith("syntax OK only (language server sent no diagnostics)"))

    def test_lsp_error_non_diag_and_diag(self):
        self.write("a.py", "x = 1\n")
        with mock.patch.object(lsp.Client, "req", side_effect=[None, TimeoutError("hover")]):
            rc, out = self.run_main("hover", "a.py", "1")
        self.assertEqual((rc, out), (3, ["lsp error: hover"]))
        with mock.patch.object(lsp.Client, "req", side_effect=[None, EOFError("server exited")]):
            rc, out = self.run_main("diag", "a.py")
        self.assertEqual(rc, 3)
        self.assertIn("lsp error: server exited", out[0])

    def test_client_config_answers(self):
        got = lsp.Client.config({"items": [{"section": "python"}, {"section": "python.analysis"}, {"section": "other"}, {}]})
        self.assertEqual(got[0], {"analysis": got[1]})
        self.assertEqual(got[2:], [{}, {}])


class HelperTests(Base):
    def test_uri_path_roundtrip(self):
        self.assertEqual(lsp.path(lsp.uri("/a b/c.py")), "/a b/c.py")

    def test_root_of(self):
        f = self.write("sub/deep/a.py", "")
        self.assertEqual(lsp.root_of(f), self.proj)

    def test_sources_skips_junk_dirs_and_respects_limit(self):
        self.write("a.py", "")
        self.write("node_modules/x.py", "")
        self.write("sub/b.py", "")
        self.write("c.js", "")
        names = sorted(os.path.relpath(p, self.proj) for p in lsp.sources(self.proj, "py"))
        self.assertEqual(names, ["a.py", os.path.join("sub", "b.py")])
        js = sorted(os.path.basename(p) for p in lsp.sources(self.proj, "ts"))
        self.assertEqual(js, ["c.js"])
        self.assertEqual(len(lsp.sources(self.proj, "py", 1)), 1)

    def test_locs_forms(self):
        r = {"uri": "file:///a.py", "range": {"start": {"line": 1, "character": 2}}}
        link = {"targetUri": "file:///b.py", "targetRange": {"start": {"line": 0, "character": 0}}}
        self.assertEqual(lsp.locs([r, r, link]), [("/a.py", 2, 3), ("/b.py", 1, 1)])
        self.assertEqual(lsp.locs(r), [("/a.py", 2, 3)])
        self.assertEqual(lsp.locs(None), [])

    def test_show_missing_file_and_empty(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lsp.show([(os.path.join(self.proj, "gone.py"), 3, 4)], self.proj)
            lsp.show([], self.proj)
        self.assertEqual(buf.getvalue().splitlines(), ["gone.py:3:4  ", "(none)"])

    def test_position_and_word_at(self):
        f = self.write("a.py", "foo = bar_baz + foo2\nfoo\n")
        self.assertEqual(lsp.position(f, "7"), (7, 1))
        self.assertEqual(lsp.position(f, "3:9"), (3, 9))
        self.assertEqual(lsp.position(f, "bar_baz"), (1, 7))
        self.assertEqual(lsp.position(f, "foo"), (1, 1))
        self.assertEqual(lsp.word_at(f, 1, 8), "bar_baz")
        self.assertEqual(lsp.word_at(f, 1, 6), "")

    def test_textrefs_none_and_dedup(self):
        self.script("rg", "echo '/p/a.js:1:2:foo'\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lsp.textrefs("foo", self.proj, "js", {("/p/a.js", 1, 2)})
        self.assertEqual(buf.getvalue(), "")


class SymbolsTests(Base):
    CTAGS = (
        '{"name":"foo","path":"%(p)s/a.py","line":3,"kind":"function"}\n'
        '{"name":"foobar","path":"%(p)s/a.py","line":9,"kind":"class","scope":"Outer"}\n'
        '{"name":"my_foo","path":"%(p)s/b.py","line":1}\n'
        '{"name":"unrelated","path":"%(p)s/b.py","line":2,"kind":"function"}\n'
        "not json\n"
    )

    def test_ranking_and_format(self):
        self.script("ctags", "cat <<'EOF'\n" + self.CTAGS % {"p": self.proj} + "EOF\n")
        rc, out = self.run_main("symbols", "foo")
        self.assertEqual(rc, 0)
        self.assertEqual(out, ["a.py:3  function foo", "a.py:9  class Outer::foobar", "b.py:1  sym my_foo"])

    def test_none_and_lang_filter(self):
        self.script("ctags", 'echo "$@" > ' + os.path.join(self.d, "args") + "\n")
        rc, out = self.run_main("symbols", "zzz", "--lang", "rs")
        self.assertEqual((rc, out), (0, ["(none)"]))
        self.assertIn("--languages=Rust", Path(self.d, "args").read_text(encoding="utf-8"))
        rc, out = self.run_main("symbols", "zzz", "--lang")  # --lang last, no value: all languages, no IndexError
        self.assertEqual(rc, 0)
        self.assertIn("--languages=Python,JavaScript,TypeScript,C,C++,Rust", Path(self.d, "args").read_text(encoding="utf-8"))
        self.run_main("symbols", "zzz", "--lang", "bogus")
        self.assertIn("--languages=Python,JavaScript,TypeScript,C,C++,Rust", Path(self.d, "args").read_text(encoding="utf-8"))


class RetryBudgetTests(unittest.TestCase):
    def test_not_ready_retries_stay_within_poll_budget(self):
        clock = [0.0]
        c = mock.Mock()
        c.req.side_effect = RuntimeError("content modified")  # rust-analyzer still loading
        with (
            mock.patch.object(lsp.time, "time", side_effect=lambda: clock[0]),
            mock.patch.object(lsp.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)),
        ):
            out = lsp._ask_locs(c, "def", "a.py", {"line": 0, "character": 0}, "textDocument/definition", 0)
        self.assertEqual(out, [])
        self.assertGreater(c.req.call_count, 1)
        self.assertLessEqual(clock[0], 2.5)  # the docstring of poll(): never longer than the timeout


class RustWaitTests(unittest.TestCase):
    def test_rust_gets_a_longer_budget(self):
        c = mock.Mock()
        self.assertEqual(lsp._open_for(c, "def", "a.rs", "rust", "."), lsp.RUST_LOAD_WAIT)
        self.assertEqual(lsp._open_for(c, "def", "a.py", "py", "."), 0)


class RobustnessTests(Base):
    def test_silent_server_hits_request_deadline(self):
        Path(self.d, "silent.py").write_text("import sys\nsys.stdin.buffer.read()\n", encoding="utf-8")  # reads, never answers
        p = subprocess.Popen([sys.executable, os.path.join(self.d, "silent.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdin.close(), p.stdout.close()))  # type: ignore[union-attr]  # PIPEs
        c = lsp.Client.__new__(lsp.Client)  # bypass initialize: only req() is under test
        c.p, c.n, c.diags, c.busy, c.buf = p, 0, {}, set(), b""
        t0 = time.time()
        with self.assertRaises(TimeoutError):
            c.req("textDocument/hover", {}, 1)
        self.assertLess(time.time() - t0, 10)

    def test_failed_initialize_terminates_server(self):
        spawned = []
        real_popen = subprocess.Popen

        def spy(*a, **k):
            p = real_popen(*a, **k)
            spawned.append(p)
            return p

        self.write("a.py", "x = 1\n")
        with mock.patch.dict(os.environ, {"FAKE_INIT_ERROR": "1"}), mock.patch.object(lsp.subprocess, "Popen", side_effect=spy):
            rc, _out = self.run_main("def", "a.py", "1")
        self.assertEqual(rc, 3)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll(), "server process left running after failed initialize")

    def test_empty_word_gives_no_text_hits(self):
        self.script("rg", "echo '/p/a.js:1:2:foo'\n")  # rg -nw "" would match everything
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lsp.textrefs("", self.proj, "js", set())
        self.assertEqual(buf.getvalue(), "")


class ReusedClientTests(Base):
    """run_op on one long-lived Client: unchanged files are not resent, edits show up in diag."""

    def client(self, lang):
        c = lsp.Client(lang, self.proj)
        self.addCleanup(c.close)
        sent = []
        real = c.note
        c.note = lambda method, params: (sent.append(method), real(method, params))[1]
        return c, sent

    def run_op(self, op, args, f, lang, c):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = lsp.run_op(op, args, f, lang, self.proj, c)
        return rc, buf.getvalue()

    def test_second_refs_does_not_resend_unchanged_files(self):
        f = self.write("a.cpp", "int foo;\nfoo;\n")
        self.write("b.cpp", "foo;\n")
        c, sent = self.client("cpp")
        self.assertEqual(self.run_op("refs", ["refs", f, "foo"], f, "cpp", c)[0], 0)
        first = sent.count("textDocument/didOpen")
        self.assertEqual(first, 2)
        self.assertEqual(self.run_op("refs", ["refs", f, "foo"], f, "cpp", c)[0], 0)
        self.assertEqual(sent.count("textDocument/didOpen"), first)

    def test_diag_after_edit_reports_fresh_result(self):
        f = self.write("a.py", "x = 1\n# BAD\n")
        c, _ = self.client("py")
        _rc, out1 = self.run_op("diag", ["diag", f], f, "py", c)
        self.assertIn("error: bad thing", out1)
        Path(f).write_text("y = 22\n", encoding="utf-8")
        _rc, out2 = self.run_op("diag", ["diag", f], f, "py", c)
        self.assertNotIn("bad thing", out2)

    def test_root_of_fallback_is_the_given_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(lsp.root_of(os.path.join(d, "x.py"), "/given"), "/given")


class ClientPieces(unittest.TestCase):
    """Client.track and Client.open need no server process."""

    def client(self):
        c = lsp.Client.__new__(lsp.Client)
        c.busy, c.docs, c.diags = set(), {}, {}
        c.lang_id = "javascript"
        c.sent = []  # type: ignore[attr-defined]  # test-only recorder on a bare Client
        c.note = lambda method, params: c.sent.append((method, params))  # type: ignore[attr-defined]
        return c

    def test_progress_begin_and_end_track_busy_tokens(self):
        c = self.client()
        c.track({"token": 7, "value": {"kind": "begin"}})
        c.track({"token": "x", "value": {"kind": "report"}})
        self.assertEqual(c.busy, {"7"})
        c.track({"token": 7, "value": {"kind": "end"}})
        c.track({"token": 9})  # no value: ignored
        self.assertEqual(c.busy, set())

    def test_open_sends_the_text_and_ts_check_prefix_only_in_memory(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.js")
            Path(f).write_text("let x;\n", encoding="utf-8")
            c = self.client()
            c.open(f)
            c.open(f, ts_check=True)
            self.assertEqual(c.sent[0][1]["textDocument"]["text"], "let x;\n")
            self.assertEqual(c.sent[1][1]["contentChanges"][0]["text"], "// @ts-check\nlet x;\n")  # flip is a didChange
            self.assertEqual(c.sent[0][0], "textDocument/didOpen")
            self.assertEqual(Path(f).read_text(encoding="utf-8"), "let x;\n")  # the file itself is untouched


if __name__ == "__main__":
    unittest.main()
