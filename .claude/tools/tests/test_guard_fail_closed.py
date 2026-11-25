import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmd_guard
import write_guard
from pipeline.configuration import ENV_GUARD_WORKTREE


def bash_payload(cmd):
    return json.dumps({"tool_input": {"command": cmd}})


class CmdGuardFailClosedTest(unittest.TestCase):
    def run_main(self, stdin_text):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin_text)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_guard.main()
        return code, out.getvalue(), err.getvalue()

    def test_unexpected_exception_denies_with_exit_2(self):
        # C1: cmd_guard is always active (no guard env), so an unexpected exception denies
        with mock.patch.object(cmd_guard, "verdict_rule", side_effect=RuntimeError("boom")):
            code, out, err = self.run_main(bash_payload("ls"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("boom", err)
        self.assertIn("denied", err)

    def test_import_failure_still_denies(self):
        # C4: the ImportError path keeps denying every Bash command
        with mock.patch.object(cmd_guard, "IMPORT_FAILURE", "cmd_guard: cannot import"):
            code, out, _ = self.run_main(bash_payload("ls"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")


class WriteGuardFailClosedTest(unittest.TestCase):
    def test_unexpected_exception_denies_with_exit_2_when_guard_active(self):
        # C2: guard active (worktree env set), an unexpected exception denies the write
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, {ENV_GUARD_WORKTREE: "/wt"}),
            mock.patch.object(write_guard, "check", side_effect=RuntimeError("boom")),
            contextlib.redirect_stderr(err),
        ):
            code = write_guard.main()
        self.assertEqual(code, 2)
        self.assertIn("boom", err.getvalue())

    def test_unexpected_exception_keeps_old_exit_when_guard_inactive(self):
        # C3: decoy, guard env not set: the exception propagates as before
        with mock.patch.dict(os.environ), mock.patch.object(write_guard, "check", side_effect=RuntimeError("boom")):
            os.environ.pop(ENV_GUARD_WORKTREE, None)
            with self.assertRaises(RuntimeError):
                write_guard.main()


if __name__ == "__main__":
    unittest.main()
