"""pipeline/claude.py: a session whose pgid record fails is killed (never left running unrecorded), and pgid_forget writes through write_text (fake `claude` on a temp PATH, no model)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude
from tests.support.tmpdirs import tracked_mkdtemp


class PgidRecordFailure(unittest.TestCase):
    def setUp(self):
        self.cwd = tracked_mkdtemp()
        self.pgid_file = os.path.join(self.cwd, "pgid")
        popen = mock.patch.object(claude.subprocess, "Popen")
        self.popen = popen.start()
        self.addCleanup(popen.stop)
        self.child = self.popen.return_value
        self.child.pid = 4242
        self.child.returncode = None
        self.kill = mock.patch.object(claude, "kill_group")
        self.kill_group = self.kill.start()
        self.addCleanup(self.kill.stop)

    def test_session_killed_when_pgid_record_fails(self):
        with mock.patch.object(claude, "pgid_record", side_effect=OSError("disk full")):
            res = claude.run_claude([], self.cwd, 5, pgid_file=self.pgid_file)
        self.kill_group.assert_called_once_with(self.child)
        self.assertTrue(res["is_error"])
        self.assertIn("disk full", res["result"])

    def test_no_kill_when_pgid_record_succeeds(self):
        with mock.patch.object(claude, "pgid_record"), mock.patch.object(claude, "_wait", return_value=("", "", False, False)):
            claude.run_claude([], self.cwd, 5, pgid_file=self.pgid_file)
        self.kill_group.assert_not_called()


class PgidForgetWritesAtomically(unittest.TestCase):
    def test_pgid_forget_goes_through_write_text(self):
        cwd = tracked_mkdtemp()
        path = os.path.join(cwd, "pgid")
        with open(path, "w", encoding="utf-8") as f:
            f.write("111\n222\n")
        with mock.patch.object(claude, "write_text", wraps=claude.write_text) as wt:
            claude.pgid_forget(path, 111)
        wt.assert_called_once_with(path, "222\n")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "222\n")

    def test_pgid_forget_removes_file_when_last_session_leaves(self):
        cwd = tracked_mkdtemp()
        path = os.path.join(cwd, "pgid")
        with open(path, "w", encoding="utf-8") as f:
            f.write("111\n")
        with mock.patch.object(claude, "write_text") as wt:
            claude.pgid_forget(path, 111)
        wt.assert_not_called()
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
