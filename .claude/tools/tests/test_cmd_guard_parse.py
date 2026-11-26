import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmd_guard

Q = "python3 -I /root/.claude/tools/qrun.py --"


class CmdGuardParseTest(unittest.TestCase):
    def test_timeout_kill_after_value_is_skipped(self):
        # C1: `-k 5` is a flag with a value; the head is the test runner after the env assignment
        self.assertIn("env", cmd_guard.verdict("timeout -k 5 10 FOO=1 python3 qrun.py -- t"))

    def test_timeout_signal_value_is_skipped(self):
        self.assertIn("env", cmd_guard.verdict("timeout -s TERM 10 FOO=1 python3 qrun.py -- t"))

    def test_sh_c_body_with_escaped_inner_quotes_is_checked(self):
        # C2: the escaped quotes do not end the body early, so the piped qrun call is seen
        cmd = 'sh -c "cs-qrun -- x \\"a\\" | tail"'
        self.assertIn("pipe", cmd_guard.verdict(cmd))

    def test_plain_timeout_test_keeps_verdict(self):
        # C4 decoy: a plain test command behind timeout is still a test run with no env prefix
        self.assertEqual(cmd_guard.verdict("timeout 10 pytest"), "")

    def test_sh_c_single_quotes_keep_verdict(self):
        # C4 decoy: single-quoted body is literal, the `;` inside it is data
        self.assertEqual(cmd_guard.verdict(f"{Q} sh -c 'a; b | c'"), "")
