"""cs-wave runs the env preflight before the loop: problems -> printed, BLOCKED.md, exit 4; none -> the loop runs; a raising probe -> logged, run goes on."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, cli_wave, driver
from pipeline import preflight as pf
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

MISSING_BIN = "no-such-binary-for-wave-preflight"
NEEDS_MISSING_BIN = f"Goal: x\nNeeds: bin:{MISSING_BIN}\nDone: ok\n"
NO_NEEDS = "Goal: x\nDone: ok\n"


class WavePreflightTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.w = f"{self.d}/.work/s"
        os.makedirs(self.w)
        self.script, self.claude = [], []
        self._o, self._i, self._c = driver.orch, driver.orch_inproc, claude.run_claude
        driver.orch, driver.orch_inproc, claude.run_claude = self.fake_orch, self.fake_orch, self.fake_claude

    def tearDown(self):
        driver.orch, driver.orch_inproc, claude.run_claude = self._o, self._i, self._c

    def fake_orch(self, cmd, stage, dir, *extra):
        if cmd == "next":
            return self.script.pop(0)
        return {"recorded": extra[1], "verdict": "DONE"}

    def fake_claude(self, args, cwd, timeout, done_file=None, env_extra=None):
        self.claude.append(args)
        return mock.Mock(text="VERDICT: DONE", cost=0.5)

    def write_task(self, text):
        Path(self.w, "task-1.md").write_text(text, encoding="utf-8")

    def run_w(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_wave.main(["run", "--stage", "s", "--dir", self.d])
        return rc, out.getvalue()

    def test_problem_prints_it_writes_blocked_and_exits_4_without_the_loop(self):
        self.write_task(NEEDS_MISSING_BIN)
        self.script = [{"action": "done", "message": "ok"}]
        with mock.patch.object(cli_wave, "run_loop", side_effect=AssertionError("loop ran")) as loop:
            rc, out = self.run_w()
        self.assertEqual(rc, 4)
        self.assertIn(f"binary not on PATH: {MISSING_BIN}", out)
        self.assertIn(f"fix: install {MISSING_BIN}", out)
        blocked = Path(self.w, pf.BLOCKED_FILE).read_text(encoding="utf-8")
        self.assertIn(f"binary not on PATH: {MISSING_BIN}", blocked)
        loop.assert_not_called()
        self.assertEqual(self.claude, [])

    def test_no_problem_runs_the_loop_as_before(self):
        self.write_task(NO_NEEDS)
        self.script = [{"action": "done", "message": "ok"}]
        with mock.patch.object(cli_wave, "run_loop", wraps=cli_wave.run_loop) as loop:
            rc, _ = self.run_w()
        self.assertEqual(rc, 0)
        loop.assert_called_once()
        self.assertFalse(os.path.exists(Path(self.w, pf.BLOCKED_FILE)))

    def test_raising_probe_is_logged_and_the_run_goes_on(self):
        self.write_task(NO_NEEDS)
        self.script = [{"action": "done", "message": "ok"}]
        err = io.StringIO()
        with mock.patch.object(pf, "run", side_effect=RuntimeError("probe broke")), mock.patch("sys.stderr", err):
            rc, _ = self.run_w()
        self.assertEqual(rc, 0)
        self.assertIn("swallowed in cli_wave.env_preflight: RuntimeError: probe broke", err.getvalue())


if __name__ == "__main__":
    unittest.main()
