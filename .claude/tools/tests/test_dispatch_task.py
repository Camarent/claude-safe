import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import driver


class DispatchTaskTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.log = os.path.join(td.name, "log.jsonl")
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ev": "dispatch", "id": "rev-1", "task": "task-1"}) + "\n")
        self.c = SimpleNamespace(log=self.log)

    def test_task_from_log_when_dispatch_has_none(self):
        self.assertEqual(driver.dispatch_task(self.c, {"id": "rev-1"}), "task-1")

    def test_task_key_wins(self):
        self.assertEqual(driver.dispatch_task(self.c, {"id": "rev-1", "task": "task-9"}), "task-9")

    def test_unknown_dispatch_gives_none(self):
        self.assertIsNone(driver.dispatch_task(self.c, {"id": "zzz"}))


if __name__ == "__main__":
    unittest.main()
