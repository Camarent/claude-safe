"""audit/ best-effort paths that swallow an error now log it through swallowed(): the stderr line names the failing call."""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import gatewatch, taskgates
from tests.support.tmpdirs import tracked_mkdtemp


def _no_git(*_args):
    """A git that cannot answer: the gates must run, so the function returns False."""
    return None


class SwallowedLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_mkdtemp()

    def test_text_of_unreadable_file_logs(self):
        rel = "gate.txt"
        with open(os.path.join(self.tmp, rel), "w", encoding="utf-8") as fh:
            fh.write("uses gate\n")
        err = io.StringIO()
        with mock.patch("audit.gatewatch.open", create=True, side_effect=OSError("unreadable")), contextlib.redirect_stderr(err):
            text = gatewatch._text_of(self.tmp, rel)
        self.assertEqual(text, "")
        self.assertIn("swallowed in gatewatch.text_of: OSError: unreadable", err.getvalue())

    def test_no_change_git_error_logs(self):
        err = io.StringIO()
        with mock.patch.object(taskgates.wt, "live_job", side_effect=OSError("git gone")), contextlib.redirect_stderr(err):
            changed = taskgates.no_change(self.tmp, "stage", "task-1", _no_git)
        self.assertFalse(changed)
        self.assertIn("swallowed in taskgates.no_change: OSError: git gone", err.getvalue())

    def test_docs_only_git_error_logs(self):
        err = io.StringIO()
        with mock.patch.object(taskgates.wt, "live_job", side_effect=RuntimeError("lock held")), contextlib.redirect_stderr(err):
            docs = taskgates.docs_only(self.tmp, "stage", "task-1", _no_git)
        self.assertFalse(docs)
        self.assertIn("swallowed in taskgates.docs_only: RuntimeError: lock held", err.getvalue())


if __name__ == "__main__":
    unittest.main()
