import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import qrun
from tests.support.tmpdirs import tracked_tmpdir


class TestParallelize(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.dir = self.tmp.name
        open(os.path.join(self.dir, "ptest.py"), "w").close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_helper(self, cmd, cwd=None):
        err = StringIO()
        with redirect_stderr(err):
            out = qrun._parallelize(cmd, cwd or self.dir)
        return out, err.getvalue()

    def test_rewrites_unittest_discover(self):
        out, err = self.run_helper(["python3", "-m", "unittest", "discover"])
        self.assertEqual(out, ["python3", "-I", "ptest.py"])
        self.assertEqual(err, "qrun: unittest discover -> ptest (parallel)\n")

    def test_no_rewrite_with_I_and_s(self):
        # ptest runs its default dir: `-s`, `-t`, `-v` would be dropped, so the command stays
        cmd = ["python3", "-I", "-m", "unittest", "discover", "-s", "tests"]
        out, err = self.run_helper(cmd)
        self.assertEqual(out, cmd)
        self.assertEqual(err, "")

    def test_rewrites_with_I_only(self):
        out, _ = self.run_helper(["python3", "-I", "-m", "unittest", "discover"])
        self.assertEqual(out, ["python3", "-I", "ptest.py"])

    def test_no_rewrite_without_ptest(self):
        with tempfile.TemporaryDirectory() as empty:
            cmd = ["python3", "-m", "unittest", "discover"]
            out, err = self.run_helper(cmd, cwd=empty)
        self.assertEqual(out, cmd)
        self.assertEqual(err, "")

    def test_no_rewrite_with_k(self):
        cmd = ["python3", "-m", "unittest", "discover", "-k", "x"]
        out, err = self.run_helper(cmd)
        self.assertEqual(out, cmd)
        self.assertEqual(err, "")

    def test_no_rewrite_with_p(self):
        cmd = ["python3", "-m", "unittest", "discover", "-p", "test_a*.py"]
        out, _ = self.run_helper(cmd)
        self.assertEqual(out, cmd)

    def test_no_rewrite_node_test(self):
        cmd = ["node", "--test", "tests/a.test.js"]
        out, err = self.run_helper(cmd)
        self.assertEqual(out, cmd)
        self.assertEqual(err, "")

    def test_no_rewrite_single_module(self):
        cmd = ["python3", "-m", "unittest", "tests.test_a"]
        out, err = self.run_helper(cmd)
        self.assertEqual(out, cmd)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()


class TestGateTimeout(unittest.TestCase):
    def test_gate_limits_stay_below_the_agent_tool_timeout(self):
        import testrun
        from pipeline.configuration import AGENT_TOOL_TIMEOUT_S, GATE_TIMEOUT_S

        self.assertLess(GATE_TIMEOUT_S, AGENT_TOOL_TIMEOUT_S)
        self.assertLess(testrun.TIMEOUT_S, AGENT_TOOL_TIMEOUT_S)
        with mock.patch.dict(os.environ, {"ORCH_SCRIPT_TIMEOUT": str(AGENT_TOOL_TIMEOUT_S * 3)}):
            self.assertEqual(qrun._timeout(), GATE_TIMEOUT_S)

    def test_short_limit_wins_and_a_backgrounded_wait_counts(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"ORCH_SCRIPT_TIMEOUT": "1"}):
            out, rc = qrun._run(["bash", "-c", "sleep 30 & wait"], os.path.join(d, "log"))
        self.assertEqual(rc, 124)
        self.assertIn("timeout after 1 s", "\n".join(out))
