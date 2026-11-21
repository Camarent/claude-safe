"""The browser check is recorded as skipped (UNVERIFIED-WEB, never silent) when headless sessions have no browser tools."""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ops_steps
from pipeline import phases, pr_ops, webskip
from pipeline.configuration import WEBTEST_SKIP_MARK, webtest_tools_missing
from tests.support.tmpdirs import tracked_mkdtemp


class MissingTest(unittest.TestCase):
    def test_default_is_available(self):
        self.assertEqual(webtest_tools_missing({}), [])

    def test_unavailable_names_the_required_prefixes(self):
        self.assertEqual(webtest_tools_missing({"webtest_tools_available": False}), ["mcp__chrome-devtools"])
        self.assertEqual(webtest_tools_missing({"webtest_tools_available": False, "webtest_requires_tools": ["mcp__x"]}), ["mcp__x"])

    def test_empty_requirement_switches_the_check_off(self):
        self.assertEqual(webtest_tools_missing({"webtest_tools_available": False, "webtest_requires_tools": []}), [])


class SkipStepTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.work = Path(self.d, ".work", "s")
        self.work.mkdir(parents=True)
        (self.work / "task-1.md").write_text("Predicted paths: web/a.js\n", encoding="utf-8")
        self.a = SimpleNamespace(stage="s", dir=self.d, feature=None)
        self.tasks = [{"id": "task-1", "wave": 1}]

    def steps(self, cfg):
        return [s for g in phases._web_test_phases(self.a, cfg, self.tasks, []) for s in g]

    def test_available_tools_dispatch_the_web_tester(self):
        self.assertEqual(self.steps({})[0]["agent"], "web-tester")

    def test_missing_tools_give_one_script_step_that_records_the_skip(self):
        steps = self.steps({"webtest_tools_available": False})
        self.assertEqual([s["agent"] for s in steps], ["script"])
        self.assertEqual(steps[0]["id"], "final-webtest")
        self.assertIn("ops_steps.py webskip", steps[0]["prompt"])
        self.assertIn("task-1", steps[0]["prompt"])
        self.assertEqual(steps[0]["ok"], {"DONE"})


class SkipNoteTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.root = Path(self.d, ".work", "s")
        (self.root / "reports").mkdir(parents=True)
        Path(self.d, "pipeline.json").write_text('{"webtest_tools_available": false}', encoding="utf-8")

    def run_cmd(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ops_steps.main(["webskip", "--dir", self.d, "--stage", "s", *extra])
        return rc, out.getvalue()

    def test_webskip_prints_done_and_the_unverified_web_line(self):
        rc, out = self.run_cmd("--ids", "task-1, task-2")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "VERDICT: DONE")
        self.assertTrue(lines[1].startswith(WEBTEST_SKIP_MARK + ": skipped: no browser tools (mcp__chrome-devtools)"))
        self.assertIn("task-1, task-2", lines[1])

    def test_digest_and_pr_note_carry_the_line(self):
        (self.root / "reports" / "report-final-webtest-r1.md").write_text("VERDICT: DONE\n" + WEBTEST_SKIP_MARK + ": skipped: x\n", encoding="utf-8")
        self.assertEqual(webskip.skips(self.d, "s"), [WEBTEST_SKIP_MARK + ": skipped: x"])
        _ok, lines = ops_steps.digest(self.d, "s")
        self.assertIn(WEBTEST_SKIP_MARK + ": skipped: x", lines)
        self.assertEqual(pr_ops.web_note(self.d, "s"), WEBTEST_SKIP_MARK + ": skipped: x")

    def test_no_skip_no_note(self):
        self.assertEqual(webskip.skips(self.d, "s"), [])
        self.assertEqual(pr_ops.web_note(self.d, "s"), "")


class ReadyBodyTest(unittest.TestCase):
    def test_ready_appends_the_note_to_the_pr_body(self):
        calls = []

        def fake(cmd, repo):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="Body.", stderr="")

        with mock.patch.object(pr_ops, "_run", side_effect=fake):
            pr_ops.append_body("/r", "br", "UNVERIFIED-WEB: skipped")
        edit = next(c for c in calls if c[:3] == ["gh", "pr", "edit"])
        self.assertIn("Body.\n\nUNVERIFIED-WEB: skipped", edit)


if __name__ == "__main__":
    unittest.main()
