import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.shellwords import split_commands, starts_command

SAMPLES = [
    "a && b || c | d ; e & f\ng",
    "cmd 2>&1 | tee x &> y; z",
    "a|b||c&&d",
    "x <& 3 >& 4 & y",
    "one\n\ntwo;",
    "",
]
OLD = {  # the separator regex each caller had before the shared helper
    "cmd_guard": (re.compile(r"\|\||\||&&|(?<![<>])&(?!>)|;|\n"), {}),
    "lp_share": (re.compile(r"&&|\|\||;|\n"), {"pipe": False, "amp": False}),
    "plan_check": (re.compile(r"&&|\|\||[;|]"), {"amp": False, "newline": False}),
}


class SplitCommandsTest(unittest.TestCase):
    def test_each_caller_rule(self):
        for name, (old, kw) in OLD.items():
            for s in SAMPLES:
                self.assertEqual(split_commands(s, **kw), old.split(s), (name, s))


class StartsCommandTest(unittest.TestCase):
    def test_matches_old_regex(self):
        old = re.compile(r"(^|[;&|]\s*)(grep|rg|egrep)\s")
        for s in ["grep x f", "ls | grep x", "a && rg y", "a;egrep z", "echo grep x", "grep", "grepx y", "ls\ngrep x", "a 2>&1 | rg q", "a || grep b"]:
            self.assertEqual(starts_command(s, ("grep", "rg", "egrep")), bool(old.search(s)), s)


if __name__ == "__main__":
    unittest.main()
