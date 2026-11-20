"""text_arg ("@file" or literal) behaves the same in local.py and localdev.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import localdev
from search import local
from tests.support.tmpdirs import tracked_mkdtemp


class TextArgTest(unittest.TestCase):
    def test_file_and_literal(self):
        p = os.path.join(tracked_mkdtemp(), "t.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("critéria")
        for fn in (local.text_arg, localdev.text_arg):
            self.assertEqual(fn("@" + p), "critéria")
            self.assertEqual(fn("plain @x"), "plain @x")
            self.assertEqual(fn(""), "")
            with self.assertRaises(OSError):
                fn("@" + p + ".missing")


if __name__ == "__main__":
    unittest.main()
