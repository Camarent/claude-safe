"""A `cmd:` acceptance line that only tests that a path exists is rejected; a command that runs behaviour is not."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.plan_rules import acceptance_problems

WHY = "only tests that a path exists"


def _problems(cmd):
    return acceptance_problems(f"## Acceptance checklist\n- cmd: {cmd}\n")


class PathOnlyCmdTest(unittest.TestCase):
    def test_path_only_cmds_are_rejected(self):
        for cmd in (
            "test -f a/b.py",
            "test -e a/b.py",
            "test -s a/b.json",
            "test -d a/b",
            "[ -f a/b.py ]",
            "ls a/b",
            "stat a/b.py",
            "grep -q name a/b.py",
            "grep -c name a/b.py",
            "test -f a/b.py | ls a/c",
        ):
            probs = _problems(cmd)
            self.assertEqual(len(probs), 1, (cmd, probs))
            self.assertIn(WHY, probs[0], cmd)
            self.assertIn("eyes:", probs[0], cmd)

    def test_chain_with_a_behaviour_is_not_path_only(self):
        # `&&` is rejected by its own rule; the path-only rule never fires for it
        probs = _problems("test -s x && scripts/dev.sh test x.py")
        self.assertEqual(len(probs), 1, probs)
        self.assertNotIn(WHY, probs[0])

    def test_commands_that_run_behaviour_are_not_rejected(self):
        for cmd in (
            "scripts/dev.sh test x.py",
            "jq -e .a f.json",
            "python3 -I check.py",
            "grep -q name file.txt extra.txt",
            "ls",
            "test x",
            "test -f",
            "cat a/b.py",
            "ls -la a/b",
            "grep -q name a/b > out.txt",
        ):
            self.assertEqual(_problems(cmd), [], cmd)
