"""util.swallowed: a swallowed error leaves one stderr line."""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import util


class Swallowed(unittest.TestCase):
    def test_one_line_with_place_and_type(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            util.swallowed("here", ValueError("boom"))
        self.assertEqual(err.getvalue(), "swallowed in here: ValueError: boom\n")


if __name__ == "__main__":
    unittest.main()
