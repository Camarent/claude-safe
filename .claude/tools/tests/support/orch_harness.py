"""Shared fixture for tests that drive orch `next`/`report` on a one-task stage in a temp dir (synthetic data)."""

import io
import json
import os
import sys
import unittest
import unittest.mock as mock
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline import cli_orch, pr_mode
from tests.support.tmpdirs import tracked_mkdtemp

TASKS = (
    "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n"
    "## Graph\nwave 1: task-1\nflags: no-merge no-test no-audit no-retro\n"
)


class OrchHarness(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        os.makedirs(f"{self.d}/.work/s/reports")
        with open(f"{self.d}/.work/s/TASKS.md", "w") as f:
            f.write(TASKS)
        env = mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"})  # restored on cleanup: a pop would also drop the module-level setting of other test files
        env.start()
        self.addCleanup(env.stop)
        for name, val in (
            ("has_origin", True),
            ("pr_active", False),
            ("direct_flow", True),
            ("sweep_active", False),
        ):  # legacy flow: an origin, no PR; tests never call gh
            pa = mock.patch.object(pr_mode, name, return_value=val)
            pa.start()
            self.addCleanup(pa.stop)
        self.config()

    def config(self, **kw):
        with open(f"{self.d}/pipeline.json", "w") as f:
            f.write(json.dumps({"gate": False, "testrun": False, "qualityrun": False, **kw}))  # the test-run step is off unless a test turns it on

    def go(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli_orch.main([args[0], "--stage", "s", "--dir", self.d, *args[1:]])
        return json.loads(out.getvalue())

    def answer(self, d, text):
        if d.get("agent") == "dev" and text.startswith("VERDICT: DONE") and "Gates:" not in text:
            text += "\nGates: all RAN ok"  # a dev report carries the Gates line when the project configures gates
        with open(f"{self.d}/.work/s/reports/report-{d['id']}.md", "w") as f:
            f.write(text)
        self.go("report", "--id", d["id"])

    def next(self):
        return self.go("next")["dispatches"][0]
