"""FIX sites of the localmodel swallow review (agent_files, askmodel, escalation_gate, plan_context): each failure leaves one swallowed() stderr line."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import agent_files, askmodel, escalation_gate, plan_context


def _boom(*_args, **_kwargs):
    raise RuntimeError("model gone")


class SwallowedLocalmodelTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name

    def _run_quiet(self, fn, *args, **kwargs):
        """-> (return value, stderr text) of one call."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = fn(*args, **kwargs)
        return out, err.getvalue()

    def test_lp_missing_binary_logs_and_returns_empty(self):
        with mock.patch.object(agent_files.subprocess, "run", side_effect=FileNotFoundError("lp")):
            out, err = self._run_quiet(agent_files._lp, self.d, "find", "x")
        self.assertEqual(out, "")
        self.assertIn("swallowed in agent_files._lp: FileNotFoundError", err)

    def test_lp_timeout_logs_and_returns_empty(self):
        timeout = subprocess.TimeoutExpired(["lp"], 1)
        with mock.patch.object(agent_files.subprocess, "run", side_effect=timeout):
            out, err = self._run_quiet(agent_files._lp, self.d, "find", "x", t=1)
        self.assertEqual(out, "")
        self.assertIn("swallowed in agent_files._lp: TimeoutExpired", err)

    def test_arch_map_corrupt_course_json_logs(self):
        Path(self.d, "course.json").write_text("{not json", encoding="utf-8")
        _, err = self._run_quiet(agent_files.arch_map, {"r": self.d})
        self.assertIn("swallowed in agent_files.arch_map course.json: JSONDecodeError", err)

    def test_arch_map_unreadable_architecture_logs(self):
        Path(self.d, "docs", "ARCHITECTURE.md").mkdir(parents=True)  # a directory: open() raises IsADirectoryError
        _, err = self._run_quiet(agent_files.arch_map, {"r": self.d})
        self.assertIn("swallowed in agent_files.arch_map ARCHITECTURE.md: IsADirectoryError", err)

    def test_arch_map_unreadable_readme_logs(self):
        Path(self.d, "README.md").mkdir()
        _, err = self._run_quiet(agent_files.arch_map, {"r": self.d})
        self.assertIn("swallowed in agent_files.arch_map README.md: IsADirectoryError", err)

    def test_arch_map_missing_docs_stay_quiet(self):
        out, err = self._run_quiet(agent_files.arch_map, {"r": self.d})
        self.assertEqual(err, "")
        self.assertIn("- r:", out)

    def test_describe_unreadable_logs_and_returns_empty(self):
        Path(self.d, "sub").mkdir()
        out, err = self._run_quiet(agent_files.describe, os.path.join(self.d, "sub"))
        self.assertEqual(out, "")
        self.assertIn("swallowed in agent_files.describe: IsADirectoryError", err)

    def test_explore_failure_logs_and_returns_none(self):
        out, err = self._run_quiet(agent_files.agent_files, {"r": self.d}, "where is x", leads="", post=_boom, arch="", listing="", tail="")
        self.assertIsNone(out)
        self.assertIn("swallowed in agent_files.explore: RuntimeError", err)

    def test_snippet_missing_file_logs_and_returns_empty(self):
        out, err = self._run_quiet(askmodel.snippet, self.d, {"path": "gone.py", "start": 1, "end": 3})
        self.assertEqual(out, "")
        self.assertIn("swallowed in askmodel.snippet: FileNotFoundError", err)

    def test_read_missing_optional_file_stays_quiet(self):
        out, err = self._run_quiet(escalation_gate.read, os.path.join(self.d, "notes.md"), 10)
        self.assertEqual((out, err), ("", ""))

    def test_read_unreadable_file_logs_and_returns_empty(self):
        out, err = self._run_quiet(escalation_gate.read, self.d, 10)  # the directory itself
        self.assertEqual(out, "")
        self.assertIn("swallowed in escalation_gate.read: IsADirectoryError", err)

    def test_gate_log_unwritable_logs_and_verdict_goes_out(self):
        Path(self.d, ".work", "s", "gate.jsonl").mkdir(parents=True)  # a directory: the append raises IsADirectoryError
        fail_file = os.path.join(self.d, "f.md")
        Path(fail_file).write_text("VERDICT: FAIL\ntests red\n", encoding="utf-8")
        (v, text), err = self._run_quiet(escalation_gate.run, "s", "task-1", self.d, fail_file, llm=lambda _p: '{"verdict":"PASS","reason":"r","evidence":""}')
        self.assertEqual(v, "DONE")
        self.assertIn("judge PASS", text)
        self.assertIn("swallowed in escalation_gate.run gate.jsonl: IsADirectoryError", err)

    def test_save_cache_failure_logs(self):
        Path(self.d, "file").write_text("", encoding="utf-8")  # a file where the cache folder should be
        _, err = self._run_quiet(plan_context._save_cache, os.path.join(self.d, "file", "cache.json"), {})
        self.assertIn("swallowed in plan_context._save_cache: FileExistsError", err)


if __name__ == "__main__":
    unittest.main()
