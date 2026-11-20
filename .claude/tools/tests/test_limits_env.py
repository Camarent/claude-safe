"""limits.stage_limit: a bad ORCH_MAX_DISPATCH falls back to the default with a warning."""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import limits
from tests.support.tmpdirs import tracked_mkdtemp

MD = "# Tasks\n\n## task-1\nGoal: x\nPredicted paths: a.py\n"


class StageLimitEnvTest(unittest.TestCase):
    def setUp(self):
        self.md = os.path.join(tracked_mkdtemp(), "TASKS.md")
        with open(self.md, "w", encoding="utf-8") as f:
            f.write(MD)

    def test_bad_env_falls_back_with_warning(self):
        default = limits.heavy_limit(self.md)[0]
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"ORCH_MAX_DISPATCH": "many"}), contextlib.redirect_stderr(err):
            self.assertEqual(limits.stage_limit(self.md), default)
        self.assertIn("ORCH_MAX_DISPATCH", err.getvalue())


if __name__ == "__main__":
    unittest.main()
