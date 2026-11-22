"""The gates: `cov` is the one test gate (pipeline.json has no separate `test`), and ptest --cov exits non-zero on a failing test."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ptest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
RED = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.fail('boom')\n"


class GateCovExitTest(unittest.TestCase):
    def setUp(self):
        logs = tempfile.TemporaryDirectory()  # failing-file logs go to a temp dir, never into the checkout
        self.addCleanup(logs.cleanup)
        patcher = mock.patch.dict(os.environ, {ptest.ENV_LOG_DIR: logs.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pipeline_gates_have_no_test_gate(self):
        with open(os.path.join(ROOT, "pipeline.json"), encoding="utf-8") as f:
            names = [g["name"] for g in json.load(f)["gates"]]
        self.assertNotIn("test", names)
        for name in ("check", "cov", "dead", "sec", "ccn"):
            self.assertIn(name, names)

    def test_cov_exits_non_zero_on_failing_test(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_r.py"), "w", encoding="utf-8") as f:
                f.write(RED)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ptest.main(["--cov", "--dir", d, "--jobs", "2"])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL test_r.py", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
