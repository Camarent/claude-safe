"""qrun characterization tests: fake node/cargo/pytest scripts on a temp PATH print captured real-world output."""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qrun
from tests.support.tmpdirs import tracked_mkdtemp

NODE_FAIL_OUT = """\
▶ suite
  ✔ good (0.5ms)
  ✖ badone (1.2ms)
✖ failing tests:

test at test/a.test.js:3:5
✖ badone (1.2ms)
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:

  1 !== 2

      at TestContext.<anonymous> (/tmp/x/test/a.test.js:4:30)
      at Test.runInAsyncScope (node:async_hooks:206:9)
      at node:internal/test_runner/test:600:20
ℹ tests 2
ℹ suites 0
ℹ pass 1
ℹ fail 1
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
"""
NODE_OK_OUT = "ℹ tests 3\nℹ suites 0\nℹ pass 3\nℹ fail 0\nℹ skipped 0\nℹ todo 0\n"
NODE_SKIP_OUT = "ℹ tests 2\nℹ pass 1\nℹ fail 0\nℹ skipped 1\nℹ todo 0\n"
NODE_ZERO_OUT = "ℹ tests 0\nℹ pass 0\nℹ fail 0\n"
TAP_OUT = """\
TAP version 13
ok 1 - good
not ok 2 - badone
  ---
  error: 'Expected values to be strictly equal'
  stack: |-
    at node:internal/foo:1:1
    at /tmp/x.js:4:5
  ...
ok 3 - after
# tests 3
# pass 2
# fail 1
"""
CARGO_FAIL_OUT = """\
running 2 tests
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
CARGO_OK_OUT = "running 2 tests\ntest a ... ok\ntest b ... ok\n\ntest result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
CARGO_IGN_OUT = "test result: ok. 1 passed; 0 failed; 2 ignored; 0 measured; 0 filtered out\n"
PYTEST_OUT = "collected 3 items\n\n=========== 2 passed, 1 skipped in 0.12s ===========\n"
PYTEST_FAIL_OUT = "FAILED t.py::test_x - assert 1 == 2\n=========== 1 failed, 4 passed in 0.30s ===========\n"


def lines(s):
    return s.splitlines()


class SummaryTest(unittest.TestCase):
    def test_node(self):
        self.assertEqual(qrun.summary(lines(NODE_OK_OUT)), ("node: 3 pass, 0 fail, 0 todo, 0 skipped", 3, 0))

    def test_node_skipped(self):
        self.assertEqual(qrun.summary(lines(NODE_SKIP_OUT))[1:], (2, 1))

    def test_tap_style_keys(self):
        self.assertEqual(qrun.summary(lines(TAP_OUT))[0], "node: 2 pass, 1 fail, 0 todo, 0 skipped")

    def test_cargo_sums_binaries(self):
        two = CARGO_OK_OUT + CARGO_IGN_OUT
        self.assertEqual(qrun.summary(lines(two)), ("cargo: 3 passed, 0 failed, 2 ignored", 5, 2))

    def test_pytest(self):
        self.assertEqual(qrun.summary(lines(PYTEST_OUT)), ("pytest: 2 passed, 1 skipped", 3, 1))
        self.assertEqual(qrun.summary(lines(PYTEST_FAIL_OUT)), ("pytest: 1 failed, 4 passed", 5, 0))

    def test_none(self):
        self.assertIsNone(qrun.summary(["hello", "world"]))
        self.assertIsNone(qrun.summary([]))


class ExcerptTest(unittest.TestCase):
    def test_node_failing_tests_block(self):
        ex = qrun.node_excerpts(lines(NODE_FAIL_OUT))
        self.assertEqual(ex[0], "test at test/a.test.js:3:5")
        self.assertIn("✖ badone (1.2ms)", ex)
        self.assertIn("      at TestContext.<anonymous> (/tmp/x/test/a.test.js:4:30)", ex)
        self.assertFalse(any("node:" in x for x in ex))  # internal frames dropped
        self.assertEqual(sum(1 for x in ex if x.strip().startswith("at ")), 1)  # first user frame only

    def test_node_block_without_test_at_prefix(self):
        ex = qrun.node_excerpts(["✖ failing tests:", "first thing", "  detail", "ℹ tests 1"])
        self.assertEqual(ex, ["first thing", "  detail"])

    def test_node_block_truncated_to_8(self):
        body = [f"  line{i}" for i in range(12)]
        ex = qrun.node_excerpts(["failing tests:", "test at x.js:1:1", *body])
        self.assertEqual(len(ex), 8)

    def test_node_tap_not_ok(self):
        ex = qrun.node_excerpts(lines(TAP_OUT))
        self.assertEqual(ex[0], "not ok 2 - badone")
        self.assertIn("  error: 'Expected values to be strictly equal'", ex)
        self.assertFalse(any("node:internal" in x for x in ex))
        self.assertNotIn("ok 3 - after", ex)

    def test_node_nothing(self):
        self.assertEqual(qrun.node_excerpts(["all good"]), [])

    def test_cargo_panic_and_failures_list(self):
        ex = qrun.cargo_excerpts(lines(CARGO_FAIL_OUT))
        self.assertEqual(ex[0], "---- bad stdout ----")
        self.assertIn("thread 'bad' panicked at src/lib.rs:2:18:", ex)
        self.assertIn("    bad", ex)
        self.assertEqual(ex[-1].split(".")[0], "test result: FAILED")

    def test_cargo_nothing(self):
        self.assertEqual(qrun.cargo_excerpts(["error[E0432]: unresolved import"]), [])

    def test_generic(self):
        ex = qrun.generic_excerpts(["setup", "ERROR boom", "ERROR boom2", "", "FAIL x"])
        self.assertEqual(ex, ["setup", "ERROR boom", "ERROR boom2", "FAIL x"])


class FakeToolTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.log = os.path.join(self.d, "q.log")
        env = mock.patch.dict(os.environ, {"PATH": self.d + os.pathsep + os.environ["PATH"]})
        env.start()
        self.addCleanup(env.stop)

    def fake(self, name, output, rc):
        out = Path(self.d, name + ".out")
        out.write_text(output, encoding="utf-8")
        script = Path(self.d, name)
        script.write_text(f"#!/bin/sh\ncat '{out}'\nexit {rc}\n", encoding="utf-8")
        script.chmod(0o755)

    def run_q(self, *cmd, extra=()):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = qrun.main(["--log", self.log, *extra, "--", *cmd])
        return rc, buf.getvalue().splitlines()

    def test_node_ok(self):
        self.fake("node", NODE_OK_OUT, 0)
        rc, out = self.run_q("node", "--test")
        self.assertEqual((rc, out), (0, [f"OK node: 3 pass, 0 fail, 0 todo, 0 skipped (log: {self.log})"]))
        self.assertIn("ℹ pass 3", Path(self.log).read_text(encoding="utf-8"))

    def test_node_skipped_is_exit_3(self):
        self.fake("node", NODE_SKIP_OUT, 0)
        rc, out = self.run_q("node", "--test")
        self.assertEqual(rc, 3)
        self.assertEqual(out[0], "SKIPPED/EMPTY: 1 skipped (node: 1 pass, 0 fail, 0 todo, 1 skipped)")

    def test_node_zero_tests(self):
        self.fake("node", NODE_ZERO_OUT, 0)
        rc, out = self.run_q("node", "--test")
        self.assertEqual(rc, 3)
        self.assertTrue(out[0].startswith("SKIPPED/EMPTY: zero tests detected"))

    def test_node_fail_excerpt(self):
        self.fake("node", NODE_FAIL_OUT, 1)
        rc, out = self.run_q("node", "--test")
        self.assertEqual(rc, 1)
        self.assertEqual(out[0], f"FAIL exit 1 (log: {self.log})")
        self.assertIn("test at test/a.test.js:3:5", out)
        self.assertEqual(out[-1], "node: 1 pass, 1 fail, 0 todo, 0 skipped")

    def test_cargo_fail_excerpt(self):
        self.fake("cargo", CARGO_FAIL_OUT, 101)
        rc, out = self.run_q("cargo", "test")
        self.assertEqual(rc, 101)
        self.assertEqual(out[0], f"FAIL exit 101 (log: {self.log})")
        self.assertIn("---- bad stdout ----", out)
        self.assertEqual(out[-1], "cargo: 1 passed, 1 failed, 0 ignored")

    def test_cargo_ok_and_ignored(self):
        self.fake("cargo", CARGO_OK_OUT, 0)
        self.assertEqual(self.run_q("cargo", "test")[1], [f"OK cargo: 2 passed, 0 failed, 0 ignored (log: {self.log})"])
        self.fake("cargo", CARGO_IGN_OUT, 0)
        rc, out = self.run_q("cargo", "test")
        self.assertEqual(rc, 3)
        self.assertIn("2 skipped", out[0])

    def test_pytest_fail_generic_excerpt(self):
        self.fake("pytest", PYTEST_FAIL_OUT, 1)
        rc, out = self.run_q("pytest")
        self.assertEqual(rc, 1)
        self.assertIn("FAILED t.py::test_x - assert 1 == 2", out)
        self.assertEqual(out[-1], "pytest: 1 failed, 4 passed")

    def test_ok_without_summary_shows_tail(self):
        self.fake("tool", "a\nb\nc\n", 0)
        _rc, out = self.run_q("tool")
        self.assertEqual(out, [f"OK exit 0 (log: {self.log})", "b", "c"])

    def test_fail_without_failword_shows_tail(self):
        self.fake("tool", "one\ntwo\n", 2)
        rc, out = self.run_q("tool")
        self.assertEqual((rc, out), (2, [f"FAIL exit 2 (log: {self.log})", "one", "two"]))

    def test_fail_with_empty_output(self):
        self.fake("tool", "", 4)
        rc, out = self.run_q("tool")
        self.assertEqual((rc, out), (4, [f"FAIL exit 4 (log: {self.log})"]))

    def test_truncation_and_ansi_strip(self):
        self.fake("tool", "".join(f"\x1b[31mERROR {i}\x1b[0m\n" for i in range(30)), 1)
        _rc, out = self.run_q("tool", extra=("--max-lines", "6"))
        self.assertEqual(len(out), 5)  # FAIL line + 3 excerpts + "... more" marker
        self.assertTrue(out[-1].startswith("... "))
        self.assertNotIn("\x1b", "\n".join(out))

    def test_missing_command_and_no_command(self):
        rc, out = self.run_q("definitely-not-a-command-xyz")
        self.assertEqual(rc, 127)
        self.assertEqual(out[0], f"FAIL exit 127 (log: {self.log})")
        self.assertIn("qrun: cannot run", out[1])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(qrun.main(["--log", self.log]), 2)
        self.assertIn("no command", err.getvalue())


if __name__ == "__main__":
    unittest.main()
