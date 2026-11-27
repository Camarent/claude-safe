"""dev_gate_audit: warnings for a dev's full-gate, repeated or slow gate runs, read from the tool-call log of its session."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import dev_gate_audit as dga
from pipeline.configuration import (
    DEV_GATE_FULL_CMDS_DEFAULT,
    DEV_GATE_REPEAT_MAX_DEFAULT,
    EV_SESSION,
    GATE_TIME_WARN_DEFAULT,
    METRICS_LOG,
    WORK_DIR,
)
from tests.support.tmpdirs import tracked_mkdtemp

FULL = DEV_GATE_FULL_CMDS_DEFAULT[0]  # the dev gate's full command (scripts/dev.sh cov)
UNIT_S = GATE_TIME_WARN_DEFAULT["unit"]
SID = "abc123-dev"
STAGE = "s1"
TASK = "task-x"


def _call(n, cmd, t0, t1=None):
    """-> (assistant record, user record) of one Bash call n started at t0; the result lands at t1 (default t0)."""
    uid = f"tu{n}"
    a = {"type": "assistant", "timestamp": t0, "message": {"content": [{"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": cmd}}]}}
    u = {"type": "user", "timestamp": t1 or t0, "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": "ok"}]}}
    return [a, u]


def _recs(*calls):
    out = []
    for c in calls:
        out += c
    return out


class AuditTest(unittest.TestCase):
    def audit(self, records, repeat_max=DEV_GATE_REPEAT_MAX_DEFAULT, unit=UNIT_S):
        return dga.audit(records, list(DEV_GATE_FULL_CMDS_DEFAULT), repeat_max, unit)

    def test_full_gate_run_warns(self):
        lines = self.audit(_recs(_call(1, FULL, "2026-01-01T00:00:00Z")))
        self.assertEqual(len(lines), 1)
        self.assertIn("full gate", lines[0])

    def test_plain_gate_command_not_full(self):
        # `scripts/dev.sh check` is not a configured full gate, and a longer word is not a whole-word match
        lines = self.audit(_recs(_call(1, "scripts/dev.sh check", "2026-01-01T00:00:00Z"), _call(2, FULL + "x", "2026-01-01T00:00:00Z")))
        self.assertEqual(lines, [])

    def test_repeat_over_max_warns(self):
        calls = [_call(i, FULL, "2026-01-01T00:00:00Z") for i in range(DEV_GATE_REPEAT_MAX_DEFAULT + 1)]
        lines = self.audit(_recs(*calls))
        self.assertTrue(any("times" in line and FULL in line for line in lines), lines)

    def test_repeat_at_max_does_not_warn_on_repeat(self):
        # repeat_max runs are allowed: only the full-gate line remains per run
        calls = [_call(i, FULL, "2026-01-01T00:00:00Z") for i in range(DEV_GATE_REPEAT_MAX_DEFAULT)]
        lines = self.audit(_recs(*calls))
        self.assertFalse(any("times" in line for line in lines), lines)

    def test_slow_call_warns(self):
        lines = self.audit(_recs(_call(1, "scripts/dev.sh test x.py", "2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z")))
        self.assertEqual(len(lines), 1)
        self.assertIn("slow", lines[0])

    def test_fast_call_does_not_warn(self):
        lines = self.audit(_recs(_call(1, "ls", "2026-01-01T00:00:00Z", "2026-01-01T00:00:10Z")))
        self.assertEqual(lines, [])

    def test_missing_timestamp_skipped(self):
        a, u = _call(1, "ls", "2026-01-01T00:00:00Z", "2026-01-01T00:09:00Z")
        del u["timestamp"]
        self.assertEqual(self.audit([a, u]), [])


class WarnTaskTest(unittest.TestCase):
    def setUp(self):
        self.ws = tracked_mkdtemp()
        self.cfg_dir = tracked_mkdtemp()
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.cfg_dir})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.makedirs(os.path.join(self.ws, WORK_DIR, STAGE))
        os.makedirs(os.path.join(self.cfg_dir, "projects", "proj"))

    def _metrics(self, rows):
        with open(os.path.join(self.ws, WORK_DIR, STAGE, METRICS_LOG), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def _transcript(self, sid, records):
        with open(os.path.join(self.cfg_dir, "projects", "proj", f"{sid}.jsonl"), "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def _row(self, agent="dev", sid=SID, ev=EV_SESSION, task=TASK):
        return {"ev": ev, "agent": agent, "sid": sid, "task": task}

    def test_full_gate_of_dev_session_warns(self):
        self._metrics([self._row()])
        self._transcript(SID, _recs(_call(1, FULL, "2026-01-01T00:00:00Z")))
        lines = dga.warn_task(self.ws, STAGE, TASK)
        self.assertTrue(any("full gate" in line for line in lines), lines)

    def test_other_agent_or_task_ignored(self):
        self._metrics([self._row(agent="reviewer"), self._row(task="task-other")])
        self._transcript(SID, _recs(_call(1, FULL, "2026-01-01T00:00:00Z")))
        self.assertEqual(dga.warn_task(self.ws, STAGE, TASK), [])

    def test_missing_metrics_gives_empty(self):
        self.assertEqual(dga.warn_task(self.ws, STAGE, TASK), [])

    def test_missing_transcript_gives_empty(self):
        self._metrics([self._row()])
        self.assertEqual(dga.warn_task(self.ws, STAGE, TASK), [])

    def test_bad_line_skipped(self):
        self._metrics([self._row()])
        path = os.path.join(self.ws, WORK_DIR, STAGE, METRICS_LOG)
        with open(path, "a", encoding="utf-8") as f:
            f.write("not json\n")
        self._transcript(SID, _recs(_call(1, FULL, "2026-01-01T00:00:00Z")))
        self.assertTrue(dga.warn_task(self.ws, STAGE, TASK))

    def test_never_raises_on_broken_config(self):
        with open(os.path.join(self.ws, "pipeline.json"), "w", encoding="utf-8") as f:
            f.write("{broken")
        self._metrics([self._row()])
        self.assertEqual(dga.warn_task(self.ws, STAGE, TASK), [])

    def test_main_prints_lines(self):
        import io
        from contextlib import redirect_stdout

        self._metrics([self._row()])
        self._transcript(SID, _recs(_call(1, FULL, "2026-01-01T00:00:00Z")))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = dga.main([STAGE, TASK, "--dir", self.ws])
        self.assertEqual(rc, 0)
        self.assertIn("full gate", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
