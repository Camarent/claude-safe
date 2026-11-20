import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import lsp
from tests.support.tmpdirs import tracked_mkdtemp

LSP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "search", "lsp.py")


def need(tool):
    """Skip a test on a host without the language server or runtime the container image carries."""
    return unittest.skipUnless(_usable(tool), f"container only: {tool} not installed or not runnable")


def _usable(tool):
    path = shutil.which(tool)
    if not path:
        return False
    try:
        return subprocess.run([path, "--version"], capture_output=True, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def diag(name, text):
    d = tracked_mkdtemp()
    Path(os.path.join(d, name)).write_text(text, encoding="utf-8")
    r = subprocess.run([sys.executable, LSP, "diag", name], cwd=d, capture_output=True, text=True, timeout=90, check=False)
    return r.returncode, r.stdout


class DiagTest(unittest.TestCase):
    def test_error_found(self):
        _rc, out = diag("bad.py", "def f(:\n    pass\n")
        self.assertIn("error", out)
        self.assertNotIn("(no diagnostics)", out)

    @need("pyright-langserver")
    def test_clean_file(self):
        rc, out = diag("ok.py", "print(1)\n")
        self.assertEqual(rc, 0)
        self.assertIn("(no diagnostics)", out)

    @need("tsc")
    def test_js_undefined_name_found(self):
        rc, out = diag("u.js", "function f(){ return undefinedVar + 1; }\n")
        self.assertEqual(rc, 0)
        self.assertIn("undefinedVar", out)
        self.assertNotIn("(no diagnostics)", out)

    @need("tsc")
    def test_js_syntax_error_found(self):
        rc, out = diag("s.js", "function f( {\n")
        self.assertEqual(rc, 0)
        self.assertIn("error", out)

    @need("tsc")
    def test_ts_type_error_found_and_clean_file(self):
        rc, out = diag("t.ts", "export const x: number = 'a';\n")
        self.assertEqual(rc, 0)
        self.assertIn("error", out)
        rc, out = diag("ok.ts", "export const y: number = 1;\n")
        self.assertEqual(rc, 0)
        self.assertIn("(no diagnostics)", out)

    @need("tsc")
    def test_esm_js_undefined_name_found(self):
        d = tracked_mkdtemp()
        os.mkdir(os.path.join(d, "src"))
        Path(os.path.join(d, "src", "undef.js")).write_text("export function g(){ return missingVar + 1 }\n", encoding="utf-8")
        r = subprocess.run([sys.executable, LSP, "diag", "src/undef.js"], cwd=d, capture_output=True, text=True, timeout=90, check=False)
        self.assertEqual(r.returncode, 0)
        self.assertIn("missingVar", r.stdout)

    @need("rust-analyzer")
    def test_rust_error_found(self):
        d = tracked_mkdtemp()
        os.mkdir(os.path.join(d, "src"))
        Path(os.path.join(d, "Cargo.toml")).write_text('[package]\nname="t"\nversion="0.1.0"\nedition="2021"\n', encoding="utf-8")
        Path(os.path.join(d, "src", "main.rs")).write_text('fn main(){ let x: i32 = "a"; }\n', encoding="utf-8")
        r = subprocess.run([sys.executable, LSP, "diag", "src/main.rs"], cwd=d, capture_output=True, text=True, timeout=120, check=False)
        self.assertIn("error", r.stdout)
        self.assertNotIn("(no diagnostics)", r.stdout)

    def test_unknown_type_is_not_checked(self):
        rc, out = diag("x.zzz", "hi\n")
        self.assertEqual(rc, 3)
        self.assertIn("NOT CHECKED", out)

    def test_no_server_falls_back_to_syntax_check(self):
        d = tracked_mkdtemp()
        f = os.path.join(d, "bad.py")
        Path(f).write_text("def f(:\n", encoding="utf-8")
        saved = lsp.SERVERS["py"]
        lsp.SERVERS["py"] = (["no-such-server-binary"], "python")
        try:
            import contextlib
            import io

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = lsp.main(["diag", f])
        finally:
            lsp.SERVERS["py"] = saved
        self.assertEqual(rc, 1)
        self.assertIn("syntax error", buf.getvalue())
        self.assertIn("NOT CHECKED", buf.getvalue())

    def test_no_server_valid_file_is_not_checked(self):
        d = tracked_mkdtemp()
        f = os.path.join(d, "ok.py")
        Path(f).write_text("x = 1\n", encoding="utf-8")
        saved = lsp.SERVERS["py"]
        lsp.SERVERS["py"] = (["no-such-server-binary"], "python")
        try:
            import contextlib
            import io

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = lsp.main(["diag", f])
        finally:
            lsp.SERVERS["py"] = saved
        self.assertEqual(rc, 3)
        self.assertIn("NOT CHECKED", buf.getvalue())


class NeedTests(unittest.TestCase):
    """need() skips when the tool is missing or its --version exits non-zero, and runs it when it exits 0."""

    def _skipped(self):
        return getattr(need("faketool")(lambda self: None), "__unittest_skip__", False)

    def test_skip_unless_runnable(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        patcher = mock.patch.dict(os.environ, {"PATH": td.name + os.pathsep + os.environ.get("PATH", "")})
        patcher.start()
        self.addCleanup(patcher.stop)
        exe = Path(td.name) / "faketool"
        exe.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        exe.chmod(0o755)
        self.assertTrue(self._skipped())
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.assertFalse(self._skipped())


class PollTests(unittest.TestCase):
    def test_returns_early_when_enough(self):
        n = []
        r = lsp.poll(lambda: n.append(1) or ([1] if len(n) >= 3 else []), bool, 5)
        self.assertEqual((r, len(n)), ([1], 3))

    def test_gives_up_at_deadline(self):
        t = lsp.time.time()
        self.assertEqual(lsp.poll(lambda: [], bool, 0.6), [])
        self.assertLess(lsp.time.time() - t, 1.5)


if __name__ == "__main__":
    unittest.main()
