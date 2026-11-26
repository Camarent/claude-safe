import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gate_record
from tests.support.tmpdirs import tracked_mkdtemp

TREE = "a" * 40


class SkipNote(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        subprocess.run(["git", "init", "-q", self.d], check=True)

    def test_note_names_log_under_common_dir_and_pass_time_not_run_id(self):
        rec = {"run": "b" * 32, "tree": TREE, "ts": 1700000000.0}
        note = gate_record.skip_note(self.d, rec)
        path = gate_record._log_path(self.d)
        self.assertIsNotNone(path)
        log = os.path.abspath(path)
        self.assertTrue(log.endswith(os.path.join(".git", gate_record.RECORD_FILE)), log)
        self.assertEqual(note, f"identical passed tree {TREE}, record {log} passed at 2023-11-14T22:13:20Z")
        self.assertNotIn(rec["run"], note)

    def test_epoch_pass_time_is_formatted_in_utc(self):
        rec = {"run": "b" * 32, "tree": TREE, "ts": 0.0}
        self.assertIn("passed at 1970-01-01T00:00:00Z", gate_record.skip_note(self.d, rec))


if __name__ == "__main__":
    unittest.main()
