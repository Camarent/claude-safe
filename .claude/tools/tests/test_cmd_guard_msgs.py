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

    def test_qrun_message_names_cs_qrun_alone_form(self):  # C1: qrun deny names the one allowed form
        rule, why = cmd_guard.verdict_rule("cs-qrun -- x | tail -n 3")
        self.assertEqual(rule, "qrun")
        self.assertIn("`cs-qrun -- <command>` alone", why)

    def test_segments_message_names_env_script_or_qrun_alone(self):  # C1: env prefix names the script-file form
        rule, why = cmd_guard.verdict_rule("VAR=x pytest -q")
        self.assertEqual(rule, "segments")
        self.assertIn("put `env` in a script file and run that file", why)

    def test_sed_message_names_one_range_form(self):  # C1: sed deny names the one print-range form
        rule, why = cmd_guard.verdict_rule("sed -n 's/a/b/p' f.txt")
        self.assertEqual(rule, "sed")
        self.assertIn("`sed -n 'A,Bp' <file>`", why)

    def test_guard_message_says_absolute_paths_no_assignment(self):  # C1: assignment deny says write absolute paths
        rule, why = cmd_guard.verdict_rule("export X=1 && pytest -q")
        self.assertEqual(rule, "segments")
        self.assertIn("Write absolute paths", why)
        self.assertIn("no variable assignment before a command", why)

    def test_cd_git_message_names_git_c_form(self):  # C1: cd deny names git -C DIR
        rule, why = cmd_guard.verdict_rule("cd /x && git status")
        self.assertEqual(rule, "cd_git")
        self.assertIn("Run `git -C /x status`", why)
        self.assertIn("instead of `cd /x` before git", why)


if __name__ == "__main__":
    unittest.main()
