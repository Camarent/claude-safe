"""pipeline_install_check: after a phase merge the driver runs the setting; a nonzero exit blocks before the next phase.

install_check takes an injected runner; the _run_phase cases patch its collaborators (planning, the stage run, done_when, the
phase state) so only the order of the steps is under test.
"""

import contextlib
import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_feature, cli_wave
from pipeline import plan_files as pf
from pipeline.configuration import STAGE_LOCK, WORK_DIR

REPO = Path(__file__).resolve().parents[3]
CFG_KEY = "pipeline_install_check"


def _cfg(command):
    return {"pipeline_install_check": command, "branch_work": "stage/{feature}"}


def _phase(d, status):
    return {"dir": d, "status": status, "depends": [], "goal": d, "done_when": "true"}


class InstallCheckFunctionTests(unittest.TestCase):
    def setUp(self):
        self.a = types.SimpleNamespace(dir="/unused", feature="f", stage="f")
        p = mock.patch.object(cli_feature, "repo_dir", return_value="/unused/repo")
        p.start()
        self.addCleanup(p.stop)

    def _check(self, command, run):
        with mock.patch.object(cli_feature, "project", return_value=_cfg(command)):
            return cli_feature.install_check(self.a, run=run)

    def test_setting_off_runs_nothing(self):
        run = mock.Mock()
        self.assertIsNone(self._check(None, run))
        run.assert_not_called()

    def test_exit_0_is_none(self):
        run = mock.Mock(return_value=subprocess.CompletedProcess("x", 0, "ok\n", ""))
        self.assertIsNone(self._check("check", run))
        run.assert_called_once()

    def test_nonzero_exit_returns_block_text_with_last_lines(self):
        out = "\n".join(f"line {i}" for i in range(8))
        run = mock.Mock(return_value=subprocess.CompletedProcess("x", 1, out, "boom\n"))
        text = self._check("check", run) or ""
        self.assertNotEqual(text, "")
        self.assertIn("installed pipeline older than repo: sync (scripts/sync-claude-config.sh --apply), then relaunch", text)
        self.assertIn("boom", text)
        self.assertNotIn("line 0", text)  # only the last 5 output lines
        self.assertIn("line 7", text)

    def test_work_branch_placeholder_is_replaced(self):
        run = mock.Mock(return_value=subprocess.CompletedProcess("x", 0, "", ""))
        self._check("scripts/sync-claude-config.sh --compare {work_branch}", run)
        self.assertEqual(run.call_args.args[0], "scripts/sync-claude-config.sh --compare stage/f")

    def test_repo_pipeline_json_sets_the_compare_check(self):
        data = json.loads((REPO / "pipeline.json").read_text(encoding="utf-8"))
        # null is temporary: pipeline.json sets the check to null until 08-finish (commit 309107b).
        self.assertIn(data[CFG_KEY], (None, "scripts/sync-claude-config.sh --compare {work_branch}"))


class RunPhaseInstallCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        tasks = os.path.join(self.tmp, "TASKS.md")
        Path(tasks).write_text("# tasks\n", encoding="utf-8")
        self.a = types.SimpleNamespace(dir=self.tmp, feature="f", stage="f", dry_run=False)
        self.cmd_run = mock.Mock(return_value=0)
        self.mark = mock.Mock()
        self.block = mock.Mock(return_value=4)
        self.check = mock.Mock(return_value=None)
        self.phases: list[dict] = []
        patches = {
            "_record_base": mock.patch.object(cli_feature, "_record_base"),
            "cmd_run": mock.patch.object(cli_feature.cli_wave, "cmd_run", self.cmd_run),
            "run_done_when": mock.patch.object(cli_feature, "run_done_when", return_value=(True, "")),
            "mark_phase_done": mock.patch.object(cli_feature, "mark_phase_done", self.mark),
            "block": mock.patch.object(cli_feature, "block", self.block),
            "install_check": mock.patch.object(cli_feature, "install_check", self.check),
            "tasks_path": mock.patch.object(pf, "tasks_path", return_value=tasks),
            "read_phases": mock.patch.object(pf, "read_phases", side_effect=lambda *_: self.phases),
        }
        for p in patches.values():
            p.start()
            self.addCleanup(p.stop)

    def test_no_next_phase_skips_the_check(self):
        # the phase run is marked done by mark_phase_done (mocked): the state after it has no open phase left
        self.phases = [_phase("01-a", "done"), _phase("02-b", "done")]
        self.assertIsNone(cli_feature._run_phase(self.a, _phase("02-b", "open")))
        self.mark.assert_called_once()
        self.check.assert_not_called()

    def test_check_passes_next_phase_runs(self):
        self.phases = [_phase("01-a", "done"), _phase("02-b", "open")]
        self.assertIsNone(cli_feature._run_phase(self.a, _phase("01-a", "open")))
        self.check.assert_called_once_with(self.a)
        self.block.assert_not_called()

    def test_check_fails_blocks_and_keeps_phase_done(self):
        self.phases = [_phase("01-a", "done"), _phase("02-b", "open")]
        self.check.return_value = "installed pipeline older than repo: sync (scripts/sync-claude-config.sh --apply), then relaunch\nx"
        rc = cli_feature._run_phase(self.a, _phase("01-a", "open"))
        self.assertEqual(rc, 4)
        self.block.assert_called_once_with(self.a, self.check.return_value)
        self.mark.assert_called_once()  # the phase stays done: the relaunch continues with 02-b


class OtherDriverTests(unittest.TestCase):
    """cli_wave.other_driver: a live driver of another stage (its wave.lock flock-held) is a rival of the install check."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _lock(self, stage, held=True):
        path = Path(self.tmp, WORK_DIR, stage, STAGE_LOCK)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        if held:  # flock is per open file description: a second fd in this process sees the conflict, as another driver would
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.addCleanup(os.close, fd)
        else:
            os.close(fd)
        return path

    def test_held_lock_of_other_stage_is_a_rival(self):
        self._lock("other", held=True)
        self.assertEqual(cli_wave.other_driver(self.tmp, "mine"), "other")

    def test_free_lock_is_no_rival_and_the_probe_takes_nothing(self):
        path = self._lock("other", held=False)
        self.assertIsNone(cli_wave.other_driver(self.tmp, "mine"))
        self.assertTrue(path.exists())  # the probe creates no file ...
        fd = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # ... and leaves no lock behind

    def test_own_stage_and_its_phases_do_not_count(self):
        self._lock("mine", held=True)
        self._lock("mine/phases/01-a", held=True)
        self.assertIsNone(cli_wave.other_driver(self.tmp, "mine"))

    def test_phase_stage_of_another_feature_is_found(self):
        self._lock("feat/phases/01-a", held=True)
        self.assertEqual(cli_wave.other_driver(self.tmp, "mine"), "feat/phases/01-a")

    def test_no_work_dir_is_no_rival(self):
        self.assertIsNone(cli_wave.other_driver(self.tmp, "mine"))

    def test_driver_retries_past_a_probe_holding_the_lock_shared(self):
        path = Path(self.tmp, "wave.lock")
        real_flock = fcntl.flock
        calls = []

        def probe_first(fd, op):  # the first attempt meets other_driver's shared probe, the next ones find the lock free
            calls.append(op)
            if len(calls) == 1:
                raise BlockingIOError(11, "probe holds the lock shared")
            return real_flock(fd, op)

        with mock.patch.object(cli_wave.fcntl, "flock", side_effect=probe_first), mock.patch.object(cli_wave.time, "sleep") as sleep:
            self.assertTrue(cli_wave._take_lock(str(path)))
        self.addCleanup(cli_wave._release_lock, str(path))
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once()

    def test_install_check_skips_while_other_driver_runs(self):
        self._lock("other", held=True)
        self._lock("mine", held=True)  # own lock: not a reason to skip
        a = types.SimpleNamespace(dir=self.tmp, feature="mine", stage="mine")
        run = mock.Mock()
        out = io.StringIO()
        with mock.patch.object(cli_feature, "project", return_value=_cfg("scripts/sync-claude-config.sh --compare")), contextlib.redirect_stdout(out):
            self.assertIsNone(cli_feature.install_check(a, run=run))
        run.assert_not_called()
        self.assertEqual(out.getvalue(), "install check skipped: driver of other running\n")

    def test_install_check_runs_when_no_other_driver(self):
        self._lock("mine", held=True)  # only our own lock
        a = types.SimpleNamespace(dir=self.tmp, feature="mine", stage="mine")
        run = mock.Mock(return_value=subprocess.CompletedProcess("x", 0, "", ""))
        with mock.patch.object(cli_feature, "project", return_value=_cfg("check")), mock.patch.object(cli_feature, "repo_dir", return_value=self.tmp):
            self.assertIsNone(cli_feature.install_check(a, run=run))
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
