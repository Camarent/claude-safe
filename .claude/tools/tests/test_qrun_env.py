import json
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import qrun
from tests.support.tmpdirs import tracked_mkdtemp


class TestEnvRoot(unittest.TestCase):
    def test_worktree_is_root(self):
        t = os.path.realpath(tracked_mkdtemp())
        json.dump({"repo": "kit", "test_env": {"SAMPLE_LIB_ROOT": "."}}, open(f"{t}/pipeline.json", "w"))
        wt = f"{t}/.wt/s/task-alpha/kit/sub"
        os.makedirs(wt)
        os.makedirs(f"{t}/kit")
        old, saved = os.getcwd(), os.environ.pop("SAMPLE_LIB_ROOT", None)
        try:
            os.chdir(f"{t}/kit")
            self.assertEqual(qrun._test_env()["SAMPLE_LIB_ROOT"], f"{t}/kit")
            os.chdir(wt)
            self.assertEqual(qrun._test_env()["SAMPLE_LIB_ROOT"], f"{t}/.wt/s/task-alpha/kit")
        finally:
            os.chdir(old)
            if saved is not None:
                os.environ["SAMPLE_LIB_ROOT"] = saved


class TestBytecodeEnv(unittest.TestCase):
    def run_echo(self):
        log = os.path.join(tracked_mkdtemp(), "q.log")
        lines, rc = qrun._run(["sh", "-c", 'printf %s "$PYTHONDONTWRITEBYTECODE"'], log)
        self.assertEqual(rc, 0)
        return lines[0] if lines else ""

    def test_commands_get_dont_write_bytecode_and_test_env_may_override(self):
        self.assertEqual(self.run_echo(), "1")
        with unittest.mock.patch.object(qrun, "_test_env", return_value={"PYTHONDONTWRITEBYTECODE": "0"}):
            self.assertEqual(self.run_echo(), "0")


class TestSubreaper(unittest.TestCase):
    def test_missing_ctypes_is_ignored(self):  # review4 L1
        saved = sys.modules.get("ctypes")
        sys.modules["ctypes"] = None  # type: ignore[assignment]  # import ctypes -> ImportError
        try:
            self.assertIsNone(qrun._become_subreaper())  # ImportError is suppressed: no raise, no return value
        finally:
            if saved is not None:
                sys.modules["ctypes"] = saved
            else:
                del sys.modules["ctypes"]
        self.assertIs(sys.modules.get("ctypes"), saved)  # sys.modules restored


if __name__ == "__main__":
    unittest.main()
