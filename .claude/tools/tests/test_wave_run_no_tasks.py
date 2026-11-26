"""cs-wave run on a stage with Plan.md and no TASKS.md: exit 4 with the feature command, the loop never runs; TASKS.md with no tasks left: exit 0 `done: no tasks`."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, cli_wave, driver
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

PLAN_ONLY_CMD = "cs-wave feature --feature s"


class WaveRunNoTasksTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.w = f"{self.d}/.work/s"
        os.makedirs(self.w)
        self.script = []
        self._o, self._i, self._c = driver.orch, driver.orch_inproc, claude.run_claude
        driver.orch, driver.orch_inproc, claude.run_claude = self.fake_orch, self.fake_orch, self.fake_claude

    def tearDown(self):
        driver.orch, driver.orch_inproc, claude.run_claude = self._o, self._i, self._c

    def fake_orch(self, cmd, stage, dir, *extra):
        if cmd == "next":
            return self.script.pop(0)
        return {"recorded": extra[1], "verdict": "DONE"}

    def fake_claude(self, args, cwd, timeout, done_file=None, env_extra=None, pgid_file=None):
        return mock.Mock(text="VERDICT: DONE", cost=0.5)

    def run_w(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_wave.main(["run", "--stage", "s", "--dir", self.d])
        return rc, out.getvalue()

    def test_plan_only_exits_4_with_the_feature_command_and_skips_the_loop(self):
        Path(self.w, "Plan.md").write_text("# plan\n", encoding="utf-8")
        with mock.patch.object(cli_wave, "run_loop", side_effect=AssertionError("loop ran")) as loop:
            rc, out = self.run_w()
        self.assertEqual(rc, 4)
        self.assertIn(f"run `{PLAN_ONLY_CMD}`", out)
        self.assertNotIn("done: no tasks", out)
        loop.assert_not_called()

    def test_tasks_file_with_no_tasks_left_is_done_with_exit_0(self):
        Path(self.w, "Plan.md").write_text("# plan\n", encoding="utf-8")
        Path(self.w, "TASKS.md").write_text("# tasks\n", encoding="utf-8")
        self.script = [{"action": "done", "message": "no tasks"}]
        rc, out = self.run_w()
        self.assertEqual(rc, 0)
        self.assertIn("done: no tasks", out)
        self.assertNotIn(PLAN_ONLY_CMD, out)
