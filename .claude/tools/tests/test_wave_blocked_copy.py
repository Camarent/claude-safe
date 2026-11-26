"""cli_wave BLOCKED paths: a blocked phase stage leaves the feature-folder BLOCKED.md naming the phase folder; a clean start removes it; the exit codes and printed status text are unchanged."""

import contextlib
import io
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_feature, cli_wave, entry
from pipeline.configuration import BLOCKED_FILE, EXIT_BLOCKED, EXIT_LIMIT
from tests.support.tmpdirs import tracked_mkdtemp


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class _Stop(Exception):
    """Raised by a patched step to end _run_locked after the clean-start unlink."""


class BlockedPhaseRunTest(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        self.feature = os.path.join(self.root, "feat")
        self.phase = os.path.join(self.feature, "phases", "01-first")
        self.a = SimpleNamespace(max_waves=None, stage=os.path.join("feat", "phases", "01-first"))
        self.c = SimpleNamespace(w=self.phase, mwave=0)

    def test_blocked_phase_writes_feature_copy(self):
        out = io.StringIO()
        with (
            mock.patch.object(cli_wave, "_ask_text", side_effect=lambda _a, msg: msg),
            mock.patch.object(cli_wave.metrics, "total_line"),
            contextlib.redirect_stdout(out),
        ):
            rc = cli_wave._blocked(self.a, self.c, {"message": "dev is stuck\n"})
        self.assertEqual(rc, EXIT_BLOCKED)
        self.assertEqual(_read(os.path.join(self.phase, BLOCKED_FILE)), "dev is stuck\n\n")
        feature_copy = _read(os.path.join(self.feature, BLOCKED_FILE))
        self.assertEqual(feature_copy.splitlines()[0], "# Blocked in phase 01-first: see 01-first/BLOCKED.md")
        self.assertIn("dev is stuck", feature_copy)
        self.assertEqual(out.getvalue(), entry.blocked_line(self.phase, "dev is stuck\n") + "\n")

    def test_max_waves_limit_returns_limit_code(self):
        self.a.max_waves = 1
        self.c.mwave = 1
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli_wave._blocked(self.a, self.c, {"message": "x"})
        self.assertEqual(rc, EXIT_LIMIT)
        self.assertEqual(out.getvalue(), "limit: max-waves 1\n")
        self.assertFalse(os.path.exists(os.path.join(self.feature, BLOCKED_FILE)))

    def test_clean_start_removes_feature_copy(self):
        entry.write_blocked(self.phase, "old block\n")
        self.assertTrue(os.path.isfile(os.path.join(self.feature, BLOCKED_FILE)))
        # every guard passes; recover_unreported stops the run right after the unlink
        with (
            mock.patch.object(cli_wave, "_plan_only", return_value=False),
            mock.patch.object(cli_wave, "reset_squash_residue", return_value=[]),
            mock.patch.object(cli_wave, "dirty_block", return_value=None),
            mock.patch.object(cli_wave, "red_base_block", return_value=None),
            mock.patch.object(cli_wave.pr_ops, "sweep_before_branch"),
            mock.patch.object(cli_wave, "ensure_work_branch", return_value=None),
            mock.patch.object(cli_wave, "recover_unreported", side_effect=_Stop),
            self.assertRaises(_Stop),
        ):
            cli_wave._run_locked(self.a, self.c)
        self.assertFalse(os.path.exists(os.path.join(self.phase, BLOCKED_FILE)))
        self.assertFalse(os.path.exists(os.path.join(self.feature, BLOCKED_FILE)))


class StatusLineTest(unittest.TestCase):
    def test_banner_and_done_text_unchanged(self):
        self.assertEqual(entry.banner("running feat/phases/01-first"), "== running feat/phases/01-first")
        self.assertEqual(entry.done_line("no open tasks"), "done: no open tasks")

    def test_run_phase_prints_running_and_done_banners(self):
        root = tracked_mkdtemp()
        folder = os.path.join(root, ".work", "feat", "phases", "01-first")
        os.makedirs(folder)
        with open(os.path.join(folder, "TASKS.md"), "w", encoding="utf-8") as fh:
            fh.write("# tasks\n")
        a = SimpleNamespace(dir=root, feature="feat", stage=None)
        phase = {"dir": "01-first", "done_when": "true"}
        out = io.StringIO()
        with (
            mock.patch.object(cli_feature, "_record_base"),
            mock.patch.object(cli_feature.cli_wave, "cmd_run", return_value=0),
            mock.patch.object(cli_feature, "manual_tag", return_value=None),
            mock.patch.object(cli_feature, "run_done_when", return_value=(True, "")),
            mock.patch.object(cli_feature, "mark_phase_done"),
            mock.patch.object(cli_feature.pf, "next_open_phase", return_value=None),
            contextlib.redirect_stdout(out),
        ):
            rc = cli_feature._run_phase(a, phase)
        self.assertIsNone(rc)
        self.assertIn("== running feat/phases/01-first\n", out.getvalue())
        self.assertIn("== phase 01-first done\n", out.getvalue())

    def test_run_loop_done_prints_done_line(self):
        a = SimpleNamespace(stage="feat/phases/01-first", dir="/unused")
        c = SimpleNamespace(w="/unused", mwave=0)
        out = io.StringIO()
        with (
            mock.patch.object(cli_wave.driver, "orch_inproc", return_value={"action": "done", "message": "no open tasks"}),
            mock.patch.object(cli_wave.metrics, "total_line"),
            contextlib.redirect_stdout(out),
        ):
            rc = cli_wave.run_loop(a, c)
        self.assertEqual(rc, 0)
        self.assertIn("done: no open tasks\n", out.getvalue())


if __name__ == "__main__":
    unittest.main()
