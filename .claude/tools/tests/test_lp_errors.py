"""lp error messages: missing file hint, symbol-not-found list, no server for a non-code file, quiet close.

The close test runs a tiny fake server that rejects shutdown when the request carries a "params" field (as real servers do).
"""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import lsp, lsp_client
from tests.support.tmpdirs import tracked_mkdtemp

# Fake server: answers initialize; answers shutdown with an error when "params" is present (JSON-RPC null params rejected).
STRICT_SHUTDOWN_SERVER = r"""
import json, sys

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

while True:
    m = read()
    if m.get("method") == "exit":
        sys.exit(0)
    if m.get("method") == "shutdown" and "params" in m:
        send({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32602, "message": "expected no params, got null"}})
    elif "id" in m:
        send({"jsonrpc": "2.0", "id": m["id"], "result": None if m.get("method") == "shutdown" else {}})
"""


class LpErrorTests(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tracked_mkdtemp())
        cwd = os.getcwd()
        os.chdir(self.d)
        self.addCleanup(os.chdir, cwd)

    def write(self, name, text):
        Path(self.d, name).write_text(text, encoding="utf-8")
        return name

    def run_main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = lsp.main(list(args))
            except SystemExit as e:
                rc = e.code
        return rc, buf.getvalue().splitlines()

    def test_missing_file_names_nearest_existing_path(self):
        self.write("lsp_main.py", "x = 1\n")
        rc, _ = self.run_main("def", "lsp_mian.py", "1")
        self.assertEqual(rc, "lsp_mian.py: no such file; nearest existing path: lsp_main.py")

    def test_missing_file_without_near_name_says_lp_find(self):
        os.mkdir(os.path.join(self.d, "empty"))
        rc, _ = self.run_main("def", "empty/zzqq.py", "1")
        self.assertEqual(rc, 'empty/zzqq.py: no such file; search for it with lp find "<question>"')

    def test_symbol_not_found_lists_file_symbols(self):
        f = self.write("a.py", "def alpha():\n    return 1\n\nclass Beta:\n    pass\n")
        with self.assertRaises(SystemExit) as cm:
            lsp.position(f, "missing")
        self.assertEqual(cm.exception.code, "symbol 'missing' not found in a.py; symbols in the file: alpha, Beta")

    def test_file_symbols_caps_at_five(self):
        text = "".join(f"def f{i}():\n    pass\n" for i in range(8))
        self.assertEqual(lsp.file_symbols(text), ["f0", "f1", "f2", "f3", "f4"])

    def test_no_server_for_markdown_exits_zero_with_hint(self):
        self.write("NOTES.md", "# notes\n")
        rc, out = self.run_main("outline", "NOTES.md")
        self.assertEqual((rc, out), (0, ["no language server for NOTES.md: use Grep or Read for this file type"]))

    def test_close_sends_no_params_and_prints_nothing(self):
        server = os.path.join(self.d, "strict_server.py")
        Path(server).write_text(STRICT_SHUTDOWN_SERVER, encoding="utf-8")
        with mock.patch.dict(lsp_client.SERVERS, {"py": ([sys.executable, server], "python")}):
            c = lsp_client.Client("py", self.d)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                c.close()
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
