import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmd_guard


class CmdGuardMessagesTest(unittest.TestCase):
    def test_qrun_pipe_message_says_run_bare_and_read_exit_code(self):  # C1: pipe after qrun names the bare run
        for cmd in ("cs-qrun -- x | tail -n 3", "cs-qrun -- x ; ls"):
            why = cmd_guard.verdict(cmd)
            self.assertIn("run the command bare and read the exit code", why, cmd)
            self.assertIn("No pipe", why, cmd)

    def test_env_prefix_message_says_where_to_set_the_variable(self):  # C2: generic env prefix on a non-qrun runner
        why = cmd_guard.verdict("VAR=x pytest -q")
        self.assertIn("Set the variable inside the test script", why)
        self.assertIn("put `env` in a script file and run that file", why)

    def test_env_prefix_message_does_not_name_denied_forms(self):  # C2: the advice does not repeat the denied shapes
        for cmd in ("VAR=x pytest -q", "FOO=1 cs-qrun -- scripts/dev.sh test a"):
            why = cmd_guard.verdict(cmd)
            self.assertNotIn("VAR=value", why, cmd)
            self.assertNotIn("`export`", why, cmd)

    def test_qrun_env_prefix_message_says_where_to_set_the_variable(self):  # C2: qrun path keeps the command and the advice
        why = cmd_guard.verdict("VAR=x cs-qrun -- scripts/dev.sh test a")
        self.assertIn("run `cs-qrun -- scripts/dev.sh test a` alone", why)
        self.assertIn("set it inside the test script", why)
        self.assertIn("put `env` in a script file", why)


if __name__ == "__main__":
    unittest.main()
