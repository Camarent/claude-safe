"""check --stage-dir: a failed tool fails the check when the stage's task Test/Done lines name it, else it is only a warning."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import extra_tools as x

TABLE_TEXT = """# Extra tools
## Accepted
| Tool | Version | Why | Install | Licence | Network use | Vetted |
|---|---|---|---|---|---|---|
| madge | 8.0.0 | cycles | `npm i --ignore-scripts madge@8.0.0` | MIT | none | ACCEPT 2026-10-24 |
"""

TASK_TEXT = """# task-1: wiring
UI: no
Predicted paths: x.py
Test: cs-qrun -- scripts/dev.sh test test_madge.py
Done: C1: madge reports cycles.
"""


def call_main(argv):
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            x.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


class TestToolProbeNeeded(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.table = os.path.join(self.tmp.name, "EXTRA-TOOLS.md")
        with open(self.table, "w") as f:
            f.write(TABLE_TEXT)
        p = mock.patch.object(x, "TABLE", self.table)
        p.start()
        self.addCleanup(p.stop)
        self.stage = os.path.join(self.tmp.name, "stage")
        os.makedirs(self.stage)
        self.audit = os.path.join(self.tmp.name, "AUDIT.md")
        with open(self.audit, "w") as f:
            f.write("uses vulture\n```audit-tools\n```\n")

    def write_task(self, text=TASK_TEXT):
        with open(os.path.join(self.stage, "task-1.md"), "w") as f:
            f.write(text)

    def test_task_test_line_names_tool_so_failure_fails(self):
        self.write_task()
        with mock.patch.object(x, "has", return_value=False):
            code, out = call_main(["check", "--kinds", "uv,npm", "--needed-by", self.audit, "--stage-dir", self.stage])
        self.assertEqual(code, 1)
        self.assertIn("VERDICT: FAIL", out)
        self.assertIn("failed: madge 8.0.0", out)

    def test_unnamed_tool_is_warning_and_check_passes(self):
        self.write_task("# task-1\nTest: pytest\nDone: C1: nothing here\n")
        with mock.patch.object(x, "has", return_value=False):
            code, out = call_main(["check", "--kinds", "npm", "--needed-by", self.audit, "--stage-dir", self.stage])
        self.assertEqual(code, 0)
        self.assertIn("VERDICT: DONE", out)
        self.assertIn("warning (not used by this project, install failed): madge 8.0.0", out)

    def test_stage_text_ignores_other_task_lines(self):
        # only Test and Done lines count: a tool named in the Predicted paths line is not needed
        self.write_task("# task-1\nPredicted paths: madge.py\nTest: pytest\n")
        self.assertEqual(x.task_text(self.stage), "Test: pytest")
        self.assertEqual(x.split_needed(["madge 8.0.0"], "", x.task_text(self.stage))[0], [])

    def test_missing_stage_dir_gives_no_stage_text(self):
        self.assertEqual(x.task_text(os.path.join(self.tmp.name, "absent")), "")
