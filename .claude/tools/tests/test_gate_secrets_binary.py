"""requires_binary: a test that needs a binary is skipped with a reason naming it when the binary is absent, and runs when it is present. The
absent case is set up by patching PATH to an empty temp dir, never by relying on the host lacking the binary. gate_secrets without gitleaks
names the missing binary on stderr."""

import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
import unittest.mock

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOOLS)
from pipeline import gate_secrets as G  # noqa: E402 - sys.path.insert above sets TOOLS
from tests.support.binaries import requires_binary  # noqa: E402 - sys.path.insert above sets TOOLS

FAKE_BIN = "fake-binary-for-tests"


def _dummy():
    return None


class TestRequiresBinary(unittest.TestCase):
    def setUp(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        self.empty_bin = empty.name

    def test_absent_binary_skip_reason_names_the_binary(self):
        with unittest.mock.patch.dict(os.environ, {"PATH": self.empty_bin}):
            decorated = requires_binary("gitleaks")(_dummy)
        reason = getattr(decorated, "__unittest_skip_why__", "")
        self.assertTrue(getattr(decorated, "__unittest_skip__", False))
        self.assertIn("gitleaks", reason)
        self.assertIn("binary", reason)

    def test_present_binary_does_not_skip(self):
        fake = os.path.join(self.empty_bin, FAKE_BIN)
        with open(fake, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        with unittest.mock.patch.dict(os.environ, {"PATH": self.empty_bin}):
            decorated = requires_binary(FAKE_BIN)(_dummy)
        self.assertFalse(getattr(decorated, "__unittest_skip__", False))


class TestMissingGitleaksNote(unittest.TestCase):
    def test_scan_without_gitleaks_names_the_binary_on_stderr(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        err = io.StringIO()
        with unittest.mock.patch.dict(os.environ, {"PATH": empty.name}), contextlib.redirect_stderr(err):
            self.assertEqual(G.scan_diff(empty.name, "HEAD"), [])
        self.assertIn(G.GITLEAKS_TOOL, err.getvalue())


if __name__ == "__main__":
    unittest.main()
