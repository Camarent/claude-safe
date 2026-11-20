import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import tracked_mkdtemp

QRUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qrun.py")
NODE_OK = "require('node:test').test('a',()=>{});"
NODE_BAD = "const t=require('node:test');t.test('good',()=>{});t.test('badone',()=>{require('assert').strictEqual(1,2)});"
NODE_SKIP = "const t=require('node:test');t.test('a',()=>{});t.test('s',{skip:true},()=>{});"
CARGO_FIX = """running 2 tests
test bad ... FAILED
test ok ... ok

failures:

---- bad stdout ----

thread 'bad' panicked at src/lib.rs:2:18:
assertion `left == right` failed
  left: 1
 right: 2

failures:
    bad

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out
"""


class T(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.log = os.path.join(self.d, "l.log")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def w(self, name, body):
        p = os.path.join(self.d, name)
        Path(p).write_text(body, encoding="utf-8")
        return p

    def run_q(self, *cmd, ml=40):
        r = subprocess.run(
            [sys.executable, "-I", QRUN, "--log", self.log, "--max-lines", str(ml), "--", *list(cmd)],
            capture_output=True,
            text=True,
            cwd=self.d,
            check=False,
        )
        return r.returncode, r.stdout

    @unittest.skipUnless(shutil.which("node"), "container only: node not installed")
    def test_node_pass(self):
        rc, out = self.run_q("node", "--test", self.w("a.test.js", NODE_OK))
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("OK node: 1 pass, 0 fail, 0 todo, 0 skipped"), out)
        self.assertEqual(len(out.splitlines()), 1)

    @unittest.skipUnless(shutil.which("node"), "container only: node not installed")
    def test_node_fail(self):
        rc, out = self.run_q("node", "--test", self.w("a.test.js", NODE_BAD))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL exit 1", out)
        self.assertIn("badone", out)
        self.assertIn("1 !== 2", out)
        self.assertIn("a.test.js:1", out)
        self.assertNotIn("node:internal", out)
        self.assertNotIn("good (", out)
        self.assertTrue(out.splitlines()[-1].startswith("node: 1 pass, 1 fail"))

    @unittest.skipUnless(shutil.which("node"), "container only: node not installed")
    def test_node_skip(self):
        rc, out = self.run_q("node", "--test", self.w("a.test.js", NODE_SKIP))
        self.assertEqual(rc, 3)
        self.assertIn("SKIPPED/EMPTY", out)

    @unittest.skipUnless(shutil.which("node"), "container only: node not installed")
    def test_node_empty(self):
        rc, out = self.run_q("node", "--test", self.w("a.test.js", "// none"))
        self.assertIn("OK" if rc == 0 else "SKIPPED/EMPTY", out)
        self.assertIn(rc, (0, 3))

    def test_cargo_fixture_fail(self):
        f = self.w("fix.txt", CARGO_FIX)
        rc, out = self.run_q("bash", "-c", f"cat {f}; exit 101")
        self.assertEqual(rc, 101)
        self.assertIn("---- bad stdout ----", out)
        self.assertIn("src/lib.rs:2:18", out)
        self.assertIn("left: 1", out)
        self.assertIn("test result: FAILED", out)
        self.assertIn("    bad", out)

    def test_cargo_fixture_pass(self):
        f = self.w("fix.txt", "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured\n")
        rc, out = self.run_q("cat", f)
        self.assertEqual(rc, 0)
        self.assertIn("OK cargo: 3 passed", out)

    def test_cargo_ignored_not_green(self):
        f = self.w("fix.txt", "test result: ok. 3 passed; 0 failed; 1 ignored; 0 measured\n")
        rc, _out = self.run_q("cat", f)
        self.assertEqual(rc, 3)

    @unittest.skipUnless(shutil.which("cargo"), "no cargo")
    def test_cargo_real(self):
        subprocess.run(["cargo", "init", "--lib", "--offline", "-q", "--name", "q"], cwd=self.d, capture_output=True, timeout=60, check=False)
        lib = os.path.join(self.d, "src", "lib.rs")
        Path(lib).write_text("#[test] fn ok(){}\n#[test] fn bad(){assert_eq!(1,2);}\n", encoding="utf-8")
        rc, out = self.run_q("cargo", "test", "--offline")
        self.assertEqual(rc, 101)
        self.assertIn("---- bad stdout ----", out)
        self.assertIn("src/lib.rs:2", out)
        Path(lib).write_text("#[test] fn ok(){}\n", encoding="utf-8")
        rc, out = self.run_q("cargo", "test", "--offline")
        self.assertEqual(rc, 0)
        self.assertIn("OK cargo: 1 passed", out)

    def test_bash_fail(self):
        s = self.w("s.sh", 'echo ctx line\necho "FAIL: thing"\necho fine\nexit 1\n')
        rc, out = self.run_q("bash", s)
        self.assertEqual(rc, 1)
        self.assertIn("ctx line\nFAIL: thing", out)
        self.assertNotIn("fine", out)

    def test_unknown_success(self):
        rc, out = self.run_q("bash", "-c", "echo a; echo b; echo c")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[1:], ["b", "c"])
        self.assertIn("OK exit 0", out)

    def test_unknown_failure_tail(self):
        rc, out = self.run_q("bash", "-c", "seq 1 100; exit 2")
        self.assertEqual(rc, 2)
        self.assertIn("100", out)
        self.assertNotIn("\n80\n", out)

    def test_noise_limited_and_log_full(self):
        _rc, out = self.run_q("bash", "-c", 'for i in $(seq 1000); do echo "FAIL noise $i"; done; exit 1', ml=20)
        self.assertLessEqual(len(out.splitlines()), 20)
        self.assertIn("more lines in", out)
        self.assertEqual(len(Path(self.log).read_text(encoding="utf-8").splitlines()), 1000)

    def test_ansi_stripped(self):
        _rc, out = self.run_q("bash", "-c", r'printf "\033[31mFAIL x\033[0m\n"; exit 1')
        self.assertNotIn("\x1b", out)

    def test_timeout_kills_a_reparented_grandchild(self):  # review3 fixes (3)
        pidfile = os.path.join(self.d, "pid")
        script = self.w("dd.sh", f"(sleep 300 & echo $! > {pidfile})\necho started\nsleep 300\n")
        with mock.patch.dict(os.environ, {"ORCH_SCRIPT_TIMEOUT": "2"}):
            rc, out = self.run_q("bash", script)
        pid = int(Path(pidfile).read_text(encoding="utf-8"))

        def alive():
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            try:  # a killed process nobody reaps (a container without init) stays a zombie: dead
                return "Z" not in Path(f"/proc/{pid}/stat").read_text().split(")")[-1][:3]
            except OSError:
                return False

        for _ in range(50):  # init reaps the killed process a moment after qrun exits
            if not alive():
                break
            time.sleep(0.1)
        if alive():
            os.kill(pid, signal.SIGKILL)
        self.assertFalse(alive() and "grandchild survived")
        self.assertEqual(rc, 124)
        self.assertIn("started", out)


if __name__ == "__main__":
    unittest.main()
