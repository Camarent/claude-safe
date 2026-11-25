import os
import signal
import sys
import tempfile
import unittest
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_wave
from pipeline.configuration import PGID_FILE_NAME


class KillStaleSessionsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="wave-stale-")
        self.addCleanup(tmp.cleanup)
        self.w = tmp.name
        with open(os.path.join(self.w, PGID_FILE_NAME), "w", encoding="utf-8") as f:
            f.write("4242\n")

    def pgid_file(self):
        return os.path.join(self.w, PGID_FILE_NAME)

    def test_permission_error_from_killpg_is_skipped(self):
        # a pid reused by another user's process: killpg raises PermissionError; no traceback, nothing counted
        with mock.patch.object(cli_wave.os, "killpg", side_effect=PermissionError(1, "Operation not permitted")) as killpg:
            self.assertEqual(cli_wave._kill_stale_sessions(self.w), 0)
        killpg.assert_called_once_with(4242, signal.SIGKILL)
        self.assertFalse(os.path.exists(self.pgid_file()))

    def test_process_lookup_error_is_still_skipped(self):
        # decoy: the group is already gone, same outcome as before the fix
        with mock.patch.object(cli_wave.os, "killpg", side_effect=ProcessLookupError(3, "No such process")):
            self.assertEqual(cli_wave._kill_stale_sessions(self.w), 0)
        self.assertFalse(os.path.exists(self.pgid_file()))

    def test_successful_kill_is_counted(self):
        with mock.patch.object(cli_wave.os, "killpg") as killpg:
            self.assertEqual(cli_wave._kill_stale_sessions(self.w), 1)
        killpg.assert_called_once_with(4242, signal.SIGKILL)
        self.assertFalse(os.path.exists(self.pgid_file()))


if __name__ == "__main__":
    unittest.main()
