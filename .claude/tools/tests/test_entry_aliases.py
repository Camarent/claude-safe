"""The one run form: `run NAME` routes to cli_feature._feature; `run --stage S` and `feature --feature F` are aliases that print one note line; `run --stage F/phases/NN` stays cmd_run."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# the sys.path line above must run before these imports
from pipeline import cli_feature, cli_wave
from pipeline import plan_files as pf
from pipeline.run import Run
from search import strata


class EntryAliasTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        self.real_feature = cli_feature._feature  # kept: the single-stage test runs the real one
        for target, attr in ((cli_wave.lead_context, "check"), (strata, "preflight")):
            mock.patch.object(target, attr).start()
            self.addCleanup(mock.patch.stopall)
        self.run_mock = mock.patch.object(cli_wave, "cmd_run", return_value=0).start()
        self.feature_mock = mock.patch.object(cli_feature, "_feature", return_value=0).start()
        self.addCleanup(mock.patch.stopall)

    def _main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli_wave.main([*argv, "--dir", self.d], extra_commands=(cli_feature.add_feature_command,))
        return rc, out.getvalue()

    def _tasks_only(self, name):
        folder = pf.feature_dir(Run(dir=self.d, feature=name))
        os.makedirs(folder, exist_ok=True)
        with open(pf.tasks_path(folder), "w", encoding="utf-8") as fh:
            fh.write("# single\n")

    def _guards_off(self):
        """Stop the git and pr guards of _feature: only the routing is under test."""
        for patcher in (
            mock.patch.object(cli_feature, "dirty_block", return_value=None),
            mock.patch.object(cli_feature, "red_base_block", return_value=None),
            mock.patch.object(cli_feature, "ensure_work_branch", return_value=None),
            mock.patch.object(cli_feature.pr_ops, "sweep_before_branch"),
            mock.patch.object(cli_feature.pf, "ensure_finish", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_run_name_calls_feature_once(self):
        # C1: `run NAME` goes through _feature once (mocked here: no phase runs)
        rc, _ = self._main(["run", "NAME"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.feature_mock.call_count, 1)
        self.assertEqual(self.feature_mock.call_args.args[0].feature, "NAME")
        self.run_mock.assert_not_called()

    def test_run_name_with_phases_runs_each_phase(self):
        # C1: the real _feature runs the open phase of a feature with phases through _run_phase
        phase = {"dir": "01-x", "done_when": "true"}
        self.feature_mock.side_effect = self.real_feature
        self._guards_off()
        pick = mock.patch.object(cli_feature, "_pick_phase", side_effect=[(phase, None), (None, 0)]).start()
        run_phase = mock.patch.object(cli_feature, "_run_phase", return_value=None).start()
        rc, _ = self._main(["run", "NAME"])
        self.assertEqual(rc, 0)
        self.assertEqual(pick.call_count, 2)
        run_phase.assert_called_once()
        self.assertEqual(run_phase.call_args.args[1], phase)
        self.run_mock.assert_not_called()

    def test_run_name_with_phase_path_is_rejected(self):
        # NAME is a feature: a phase path in NAME is an argparse error, not a feature dir
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
            self._main(["run", "F/phases/01-x"])
        self.assertIn("--stage F/phases/NN", err.getvalue())
        self.feature_mock.assert_not_called()
        self.run_mock.assert_not_called()

    def test_run_name_single_stage_runs_stage_through_cmd_run(self):
        # C1: TASKS.md only: the real _feature runs the single stage with cmd_run
        self.feature_mock.side_effect = self.real_feature
        self._guards_off()
        self._tasks_only("NAME")
        rc, _ = self._main(["run", "NAME"])
        self.assertEqual(rc, 0)
        self.run_mock.assert_called_once()
        self.assertEqual(self.run_mock.call_args.args[0].stage, "NAME")

    def test_run_stage_plain_name_prints_note_and_takes_name_path(self):
        # C2: old form `run --stage S` prints one note line, then the NAME path
        rc, out = self._main(["run", "--stage", "S"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("note:"), 1)
        self.assertIn("run --stage S", out)
        self.assertEqual(self.feature_mock.call_args.args[0].feature, "S")

    def test_feature_flag_prints_note_and_takes_name_path(self):
        # C2: old form `feature --feature F` prints one note line, then the same _feature call
        rc, out = self._main(["feature", "--feature", "F"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("note:"), 1)
        self.assertIn("feature --feature F", out)
        self.assertEqual(self.feature_mock.call_args.args[0].feature, "F")

    def test_run_stage_phase_path_stays_cmd_run(self):
        # C3: the driver's phase path goes straight to cmd_run, no note, no _feature
        rc, out = self._main(["run", "--stage", "F/phases/01-x"])
        self.assertEqual(rc, 0)
        self.assertNotIn("note:", out)
        self.assertEqual(self.run_mock.call_args.args[0].stage, "F/phases/01-x")
        self.feature_mock.assert_not_called()

    def test_run_stage_without_feature_hook_stays_cmd_run(self):
        # no hook wired (library callers): the stage form still runs cmd_run, no note, no _feature
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = cli_wave.main(["run", "--stage", "S", "--dir", self.d])
        self.assertEqual(rc, 0)
        self.assertNotIn("note:", out.getvalue())
        self.run_mock.assert_called_once()
        self.feature_mock.assert_not_called()

    def test_run_needs_name_or_stage(self):
        # neither NAME nor --stage: argparse error
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self._main(["run"])


if __name__ == "__main__":
    unittest.main()
