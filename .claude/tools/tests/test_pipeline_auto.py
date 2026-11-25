"""scripts/pipeline_auto.py: auto permission mode for cs-wave, same flags as BASE_FLAGS except the mode; no literal flag or env name in the script."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pipeline_auto  # noqa: E402  (scripts/ is put on sys.path above)
from pipeline.configuration import (  # noqa: E402  (.claude/tools is put on sys.path above)
    BASE_FLAGS,
    DEFAULT_FLAGS,
    ENV_CLAUDE_FLAGS,
    PERMISSION_MODE_AUTO,
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_FLAG,
)

SCRIPT_SOURCE = os.path.join(ROOT, "scripts", "pipeline_auto.py")


class AutoFlagsTests(unittest.TestCase):
    def test_mode_is_auto_not_accept_edits(self):
        flags = pipeline_auto.auto_flags()
        self.assertIn(f"{PERMISSION_MODE_FLAG} {PERMISSION_MODE_AUTO}", flags)
        self.assertNotIn(PERMISSION_MODE_DEFAULT, flags)

    def test_only_the_mode_differs_from_base(self):
        base_rest = DEFAULT_FLAGS.replace(f"{PERMISSION_MODE_FLAG} {PERMISSION_MODE_DEFAULT}", "", 1)
        auto_rest = pipeline_auto.auto_flags().replace(f"{PERMISSION_MODE_FLAG} {PERMISSION_MODE_AUTO}", "", 1)
        self.assertEqual(base_rest, auto_rest)

    def test_allowlist_survives(self):
        self.assertGreater(len(DEFAULT_FLAGS), len(BASE_FLAGS))
        tail = DEFAULT_FLAGS[len(BASE_FLAGS) :]
        self.assertTrue(tail)
        self.assertIn(tail, pipeline_auto.auto_flags())

    def test_base_without_default_mode_fails(self):
        with self.assertRaises(ValueError):
            pipeline_auto.auto_flags("--allowedTools Read")


class PlanTests(unittest.TestCase):
    def test_feature_call_sets_auto_flags_env(self):
        argv, env = pipeline_auto.plan(["feature", "--feature", "F"], {"KEEP": "1"})
        self.assertEqual(argv, [pipeline_auto.CS_WAVE, "feature", "--feature", "F"])
        self.assertIn(PERMISSION_MODE_AUTO, env[ENV_CLAUDE_FLAGS])
        self.assertNotIn(PERMISSION_MODE_DEFAULT, env[ENV_CLAUDE_FLAGS])
        self.assertEqual(env["KEEP"], "1")

    def test_run_stage_call(self):
        argv, _ = pipeline_auto.plan(["run", "--stage", "S"], {})
        self.assertEqual(argv, [pipeline_auto.CS_WAVE, "run", "--stage", "S"])

    def test_bad_first_word_rejected(self):
        with self.assertRaises(ValueError):
            pipeline_auto.plan(["rm", "-rf"], {})
        with self.assertRaises(ValueError):
            pipeline_auto.plan([], {})

    def test_environ_not_mutated(self):
        environ = {"A": "1"}
        pipeline_auto.plan(["feature"], environ)
        self.assertEqual(environ, {"A": "1"})


class MainTests(unittest.TestCase):
    def test_main_execs_cs_wave_with_auto_env(self):
        with mock.patch("os.execve") as execve:
            pipeline_auto.main(["run", "--stage", "S"], {})
        execve.assert_called_once()
        path, argv, env = execve.call_args.args
        self.assertEqual(path, pipeline_auto.CS_WAVE)
        self.assertEqual(argv, [pipeline_auto.CS_WAVE, "run", "--stage", "S"])
        self.assertIn(PERMISSION_MODE_AUTO, env[ENV_CLAUDE_FLAGS])

    def test_main_bad_args_no_exec(self):
        with mock.patch("os.execve") as execve, mock.patch("sys.stderr"):
            code = pipeline_auto.main(["nope"], {})
        self.assertEqual(code, 2)
        execve.assert_not_called()


class NoLiteralsTests(unittest.TestCase):
    def test_source_has_no_flag_or_env_literal(self):
        with open(SCRIPT_SOURCE, encoding="utf-8") as fh:
            src = fh.read()
        for literal in ("--permission-mode", "acceptEdits", '"auto"', "WAVE_CLAUDE_FLAGS", '"cs-wave"', '"bin"', '"feature"', '"run"'):
            self.assertNotIn(literal, src)


if __name__ == "__main__":
    unittest.main()
