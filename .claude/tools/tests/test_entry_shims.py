"""The top-level entry scripts (wave.py, plan.py, orch.py, xtools.py) run their package main when executed as a script."""

import contextlib
import io
import os
import runpy
import sys
import unittest
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class EntryShimTest(unittest.TestCase):
    def run_shim(self, name):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", [name, "--help"]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                runpy.run_path(os.path.join(TOOLS, name), run_name="__main__")
            except SystemExit as e:
                self.assertIn(e.code, (0, None), out.getvalue()[-300:])
        return out.getvalue()

    def test_each_entry_exits_cleanly_on_help(self):
        for name in ("wave.py", "plan.py", "orch.py", "xtools.py"):
            with self.subTest(name):
                self.assertNotIn("Traceback", self.run_shim(name))


if __name__ == "__main__":
    unittest.main()
