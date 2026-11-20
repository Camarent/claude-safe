"""util.add_common_args builds the --dir/--stage options the step scripts declared by hand."""

import argparse
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import util


def parse(argv, **kw):
    ap = argparse.ArgumentParser()
    util.add_common_args(ap, **kw)
    return ap.parse_args(argv)


class CommonArgsTest(unittest.TestCase):
    def test_dir_only(self):
        self.assertEqual(parse([]).dir, ".")
        self.assertEqual(parse(["--dir", "x"]).dir, "x")
        self.assertFalse(hasattr(parse([]), "stage"))

    def test_stage_required(self):
        self.assertEqual(parse(["--stage", "s"], stage=True).stage, "s")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse([], stage=True)

    def test_dir_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse(["--stage", "s"], stage=True, dir_required=True)
        self.assertEqual(parse(["--dir", "d", "--stage", "s"], stage=True, dir_required=True).dir, "d")

    def test_dir_default(self):
        self.assertEqual(parse([], dir_default="/w").dir, "/w")


if __name__ == "__main__":
    unittest.main()
