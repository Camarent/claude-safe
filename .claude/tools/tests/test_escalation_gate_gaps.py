"""escalation_gate edge paths: no failure files, security ESCALATE bypass, no usable model answer, unwritable log, main()."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import escalation_gate


class GapsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        Path(self.d, ".work", "s").mkdir(parents=True)
        Path(self.d, ".work", "s", "task-1.md").write_text("Goal: demo\n", encoding="utf-8")
        self.fail_file = os.path.join(self.d, "f.md")
        Path(self.fail_file).write_text("VERDICT: ESCALATE: writes outside the path allow-list\n", encoding="utf-8")

    def test_judge_failure_is_reported_and_fails_open(self):  # audit: broad except reports through swallowed
        def boom(_prompt):
            raise RuntimeError("model gone")

        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(escalation_gate.judge("p", llm=boom), {})
        self.assertIn("swallowed in escalation_gate.judge: RuntimeError", err.getvalue())

    def test_read_failures_empty(self):
        self.assertEqual(escalation_gate.read_failures([]), ("", ""))
        self.assertEqual(escalation_gate.read_failures([None, ""]), ("", ""))

    def test_security_escalate_is_never_judged(self):
        llm = mock.Mock()
        v, text = escalation_gate.run("s", "task-1", self.d, self.fail_file, llm=llm)
        llm.assert_not_called()
        self.assertEqual(v, "DONE")
        self.assertIn("judge PASS", text)
        row = json.loads(Path(self.d, ".work", "s", "gate.jsonl").read_text(encoding="utf-8"))
        self.assertEqual((row["judge"], row["veto"], row["task"]), ("PASS", False, "task-1"))

    def test_no_usable_answer_skips_the_gate(self):
        Path(self.fail_file).write_text("VERDICT: FAIL\ntests red\n", encoding="utf-8")
        v, text = escalation_gate.run("s", "task-1", self.d, self.fail_file, llm=lambda p: "not json")
        self.assertEqual(v, "DONE")
        self.assertIn("gate skipped", text)
        row = json.loads(Path(self.d, ".work", "s", "gate.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(row["judge"], "NONE")

    def test_unwritable_log_does_not_break_the_gate(self):
        Path(self.fail_file).write_text("VERDICT: FAIL\ntests red\n", encoding="utf-8")
        with mock.patch("builtins.open", side_effect=_open_except_gate_log()):
            v, text = escalation_gate.run("s", "task-1", self.d, self.fail_file, llm=lambda p: '{"verdict":"PASS","reason":"r","evidence":""}')
        self.assertEqual(v, "DONE")
        self.assertIn("judge PASS", text)
        self.assertFalse(Path(self.d, ".work", "s", "gate.jsonl").exists())

    def test_main_prints_verdict(self):
        buf = io.StringIO()
        with mock.patch.object(escalation_gate, "run", return_value=("DONE", "ok")), contextlib.redirect_stdout(buf):
            rc = escalation_gate.main(["--stage", "s", "--task", "task-1", "--dir", self.d, "--failure", self.fail_file])
        self.assertEqual((rc, buf.getvalue()), (0, "VERDICT: DONE\nok\n"))


def _open_except_gate_log():
    real = open

    def fake(path, *a, **k):
        if str(path).endswith("gate.jsonl"):
            raise OSError("read-only")
        return real(path, *a, **k)

    return fake


if __name__ == "__main__":
    unittest.main()
