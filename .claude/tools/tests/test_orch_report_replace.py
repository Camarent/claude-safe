"""orch report --replace --text overwrites an existing report and records its new verdict; without the flag --text never overwrites."""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch
from pipeline.configuration import EV_DISPATCH, EV_REPORT, MODEL_HAIKU, ORCH_LOG
from tests.support.tmpdirs import tracked_mkdtemp

STAGE = "s"
TASK = "task-1"
ID = "a1"
BLOCKED_BODY = "VERDICT: BLOCKED\nneeds a researcher answer\n"
DONE_BODY = "VERDICT: DONE\nresearcher answer: all green\n"


class ReportReplaceTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.work = Path(self.d, ".work", STAGE)
        self.work.mkdir(parents=True)
        self.log = self.work / ORCH_LOG
        self.log.write_text(
            json.dumps({"ev": EV_DISPATCH, "id": ID, "task": TASK, "role": "dev", "agent": "dev", "model": MODEL_HAIKU}) + "\n", encoding="utf-8"
        )
        self.report = self.work / "reports" / f"report-{ID}.md"

    def args(self, text=None, replace=False):
        return SimpleNamespace(dir=self.d, stage=STAGE, id=ID, text=text, replace=replace)

    def run_report(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            cli_orch.cmd_report(self.args(**kw))
        evs = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]
        return [e for e in evs if e["ev"] == EV_REPORT][-1]

    def test_replace_overwrites_blocked_report_and_records_done(self):
        self.assertEqual(self.run_report(text=BLOCKED_BODY)["verdict"], "BLOCKED")
        ev = self.run_report(text=DONE_BODY, replace=True)
        self.assertEqual(ev["verdict"], "DONE")
        self.assertEqual(self.report.read_text(encoding="utf-8"), DONE_BODY)

    def test_without_replace_text_never_overwrites(self):
        self.assertEqual(self.run_report(text=BLOCKED_BODY)["verdict"], "BLOCKED")
        ev = self.run_report(text=DONE_BODY)
        self.assertEqual(ev["verdict"], "BLOCKED")
        self.assertEqual(self.report.read_text(encoding="utf-8"), BLOCKED_BODY)

    def test_replace_without_text_exits(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            cli_orch.cmd_report(self.args(replace=True))
        self.assertFalse(self.report.exists())


if __name__ == "__main__":
    unittest.main()
