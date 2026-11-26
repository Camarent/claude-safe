"""red_base_block: the gates run on a fresh base; red refuses the start, green is marked per HEAD, resume and dry run skip; the flag is gone from the three parsers."""

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import final_check
from pipeline import branches, cli_feature, cli_plan, cli_wave
from pipeline.configuration import PREFLIGHT_MARKER, ConfigError
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp


class RedBasePreflightTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.addCleanup(self._rm)
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "seed")
        self.set_gates([("green", "true")])
        self.a = argparse.Namespace(dir=self.d, stage="s1", feature="", dry_run=False)
        self.calls: list = []
        real = final_check.run_all

        def spy(*args, **kw):
            self.calls.append(args[0])
            return real(*args, **kw)

        patch = mock.patch.object(final_check, "run_all", side_effect=spy)
        patch.start()
        self.addCleanup(patch.stop)

    def _rm(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def set_gates(self, gates, extra=None):
        cfg = {"gates": [{"name": n, "cmd": c} for n, c in gates]}
        cfg.update(extra or {})
        with open(os.path.join(self.d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def folder(self):
        return os.path.join(self.d, ".work", "s1")

    def marker(self):
        return os.path.join(self.folder(), PREFLIGHT_MARKER)

    def head(self):
        return git(self.d, "rev-parse", "HEAD")

    def test_each_groups_reach_run_all(self):
        os.makedirs(os.path.join(self.d, "w", "c1"))
        open(os.path.join(self.d, "w", "c1", "course.json"), "w").close()
        self.set_gates([("green", "true")], {"each": [{"glob": "w/*/course.json", "cmd": "true {dir}", "parallel": "js"}]})
        seen = []
        with mock.patch.object(final_check, "run_all", side_effect=lambda cmds, *a, **kw: seen.append(kw.get("groups")) or []):
            branches.red_base_block(self.a)
        self.assertEqual(seen, [{"true w/c1": "js"}])

    def test_green_base_passes_and_writes_marker(self):
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(len(self.calls), 1)
        with open(self.marker(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"head": self.head()})

    def test_red_gate_is_named_and_writes_no_marker(self):
        self.set_gates([("green", "true"), ("broken", "false")])
        msg = branches.red_base_block(self.a)
        self.assertEqual(msg.splitlines()[-1], "blocked: base is red: broken; fix the base first", msg)
        self.assertIn("gate broken: FAILED", msg)
        self.assertLessEqual(len(msg), 1500)
        self.assertFalse(os.path.exists(self.marker()))

    def test_missing_binary_line_names_binaries_above_last_line(self):
        assertion = "AssertionError: ffmpeg and ffprobe must be installed to run these tests"
        self.set_gates([("media", f"echo '{assertion}' >&2; exit 1")])
        msg = branches.red_base_block(self.a)
        lines = msg.splitlines()
        self.assertEqual(lines[-1], "blocked: base is red: media; fix the base first", msg)
        self.assertEqual(lines[-2], "missing binaries: ffmpeg, ffprobe (install them, then rerun)", msg)
        self.assertIn(assertion, msg)

    def test_no_missing_binary_line_without_marker(self):
        self.set_gates([("broken", "echo 'ffmpeg is slow' >&2; exit 1")])
        msg = branches.red_base_block(self.a)
        self.assertNotIn("missing binaries", msg)
        self.assertEqual(msg.splitlines()[-1], "blocked: base is red: broken; fix the base first", msg)

    def test_missing_binaries_setting_overrides_list(self):
        assertion = "AssertionError: ffmpeg and ffprobe must be installed to run these tests"
        self.set_gates([("media", f"echo '{assertion}' >&2; exit 1")], {"missing_binaries": ["ffprobe", "sox"]})
        msg = branches.red_base_block(self.a)
        self.assertIn("missing binaries: ffprobe (install them, then rerun)", msg)
        self.assertNotIn("sox", msg)

    def test_merge_only_gates_run_in_the_preflight(self):
        self.set_gates([("green", "true")], {"gates_merge": [{"name": "slow", "cmd": "false"}]})
        self.assertIn("gate slow: FAILED", branches.red_base_block(self.a))  # the base must pass what the final test runs

    def test_a_broken_config_is_reported(self):
        with open(os.path.join(self.d, "pipeline.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertRaises(ConfigError):
            branches.red_base_block(self.a)

    def test_only_merge_gates_configured_still_run_in_the_preflight(self):
        self.set_gates([], {"gates_merge": [{"name": "slow", "cmd": "false"}]})
        self.assertIn("gate slow: FAILED", branches.red_base_block(self.a))

    def test_dry_run_skips_gates(self):
        self.set_gates([("broken", "false")])
        self.a.dry_run = True
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(self.calls, [])

    def test_setting_false_skips_gates(self):
        self.set_gates([("broken", "false")], {"preflight_gates": False})
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(self.calls, [])

    def test_no_gates_skips(self):
        self.set_gates([])
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(self.calls, [])

    def test_resume_with_orch_log_skips_gates(self):
        os.makedirs(self.folder())
        open(os.path.join(self.folder(), "orch.jsonl"), "w").close()
        self.set_gates([("broken", "false")])
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(self.calls, [])

    def test_feature_phase_log_skips_gates_and_gate_does_not_run(self):
        self.a.stage, self.a.feature = "", "F"
        os.makedirs(os.path.join(self.d, ".work", "F", "phases", "01-x"))
        open(os.path.join(self.d, ".work", "F", "phases", "01-x", "orch.jsonl"), "w").close()
        self.set_gates([("broken", "false"), ("flag", "touch ran.flag")])
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.d, "ran.flag")))

    def test_next_phase_stage_with_feature_log_skips_gates(self):
        self.a.stage, self.a.feature = "F/phases/02-y", ""
        os.makedirs(os.path.join(self.d, ".work", "F", "phases", "01-x"))
        open(os.path.join(self.d, ".work", "F", "phases", "01-x", "orch.jsonl"), "w").close()
        self.set_gates([("broken", "false")])
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(self.calls, [])

    def test_first_phase_on_empty_feature_folder_writes_marker_at_feature_root(self):
        self.a.stage, self.a.feature = "F/phases/01-x", ""
        os.makedirs(os.path.join(self.d, ".work", "F"))
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(os.path.exists(os.path.join(self.d, ".work", "F", PREFLIGHT_MARKER)))
        self.assertFalse(os.path.exists(os.path.join(self.d, ".work", "F", "phases", "01-x", PREFLIGHT_MARKER)))

    def test_marker_with_same_head_skips_gates(self):
        os.makedirs(self.folder())
        with open(self.marker(), "w", encoding="utf-8") as f:
            json.dump({"head": self.head()}, f)
        self.set_gates([("broken", "false")])
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(self.calls, [])

    def test_marker_with_other_head_runs_again(self):
        os.makedirs(self.folder())
        with open(self.marker(), "w", encoding="utf-8") as f:
            json.dump({"head": "0" * 40}, f)
        self.assertEqual(branches.red_base_block(self.a), "")
        self.assertEqual(len(self.calls), 1)


class RedBaseFlagGoneTest(unittest.TestCase):
    """--allow-red-base no longer exists: a red base always blocks."""

    def assert_parse_fails(self, parse, argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse(argv)

    def test_plan_run_rejects_flag(self):
        self.assert_parse_fails(cli_plan.build().parse_args, ["run", "--feature", "f", "--prompt-file", "p", "--allow-red-base"])

    def test_wave_run_rejects_flag(self):
        self.assert_parse_fails(cli_wave.main, ["run", "--stage", "s", "--dir", ".", "--dry-run", "--allow-red-base"])

    def test_feature_rejects_flag(self):
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="cmd", required=True)
        cli_feature.add_feature_command(sub)
        self.assert_parse_fails(ap.parse_args, ["feature", "--feature", "f", "--allow-red-base"])


if __name__ == "__main__":
    unittest.main()
