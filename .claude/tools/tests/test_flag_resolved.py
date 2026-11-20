"""A merge-fix's RESOLVED lines are logged once: decide() re-walks earlier waves, so the same report is read again on every `orch next`."""

import json
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import graph
from pipeline.util import read_jsonl
from tests.support.tmpdirs import tracked_mkdtemp


class FlagResolvedTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="synthetic-project-")
        self.w = os.path.join(self.d, ".work", "s")
        os.makedirs(os.path.join(self.w, "reports"))
        open(os.path.join(self.w, "TASKS.md"), "w", encoding="utf-8").close()
        self.log = os.path.join(self.w, "orch.jsonl")
        self.rep = os.path.join(self.w, "reports", "report-a1.md")
        with open(self.rep, "w", encoding="utf-8") as f:
            f.write("merged\nRESOLVED: a.py: both: kept the new import\nRESOLVED: b.py: dev: took dev\n")
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ev": "report", "task": "merge-fix-w1", "id": "a1", "file": self.rep}) + "\n")
        self.a = SimpleNamespace(dir=self.d, stage="s")

    def resolved(self):
        return [e["line"] for e in read_jsonl(self.log) if e["ev"] == "resolved"]

    def test_each_resolved_line_is_logged_once_however_often_it_runs(self):
        graph._flag_resolved(self.a, 1)
        graph._flag_resolved(self.a, 1)  # the next `orch next` walks the same wave again
        self.assertEqual(self.resolved(), ["RESOLVED: a.py: both: kept the new import", "RESOLVED: b.py: dev: took dev"])


if __name__ == "__main__":
    unittest.main()
