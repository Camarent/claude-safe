"""cmd_guard deny reasons: line 1 of every reason names the allowed form; a cd before git is rewritten silently, and a rewrite that stays unsafe still denies."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmd_guard
from pipeline.configuration import ENV_SCRATCH_DIR

MAIN_CWD = "/repo/main"


def first_line(why):
    return why.splitlines()[0]


class FirstLineNamesAllowedFormTest(unittest.TestCase):
    def test_qrun_pipe_line1_names_cs_qrun_alone(self):  # C1: qrun reason
        rule, why = cmd_guard.verdict_rule("cs-qrun -- x | tail -n 3")
        self.assertEqual(rule, "qrun")
        self.assertIn("`cs-qrun -- <command>` alone", first_line(why))

    def test_cd_git_line1_names_git_c_form(self):  # C1: cd reason
        with mock.patch("os.getcwd", return_value=MAIN_CWD):
            rule, why = cmd_guard.verdict_rule("cd /x && git status")
        self.assertEqual(rule, "cd_git")
        self.assertIn("`git -C /x status`", first_line(why))

    def test_env_qrun_line1_names_cs_qrun_alone(self):  # C1: env prefix on qrun
        rule, why = cmd_guard.verdict_rule("VAR=x cs-qrun -- scripts/dev.sh test a")
        self.assertEqual(rule, "segments")
        self.assertIn("`cs-qrun -- scripts/dev.sh test a` alone", first_line(why))

    def test_env_test_runner_line1_names_qrun_py_form(self):  # C1: env prefix on a plain runner
        rule, why = cmd_guard.verdict_rule("VAR=x pytest -q")
        self.assertEqual(rule, "segments")
        self.assertIn("qrun.py -- <command>", first_line(why))

    def test_export_line1_names_absolute_paths(self):  # C1: assignment reason
        rule, why = cmd_guard.verdict_rule("export X=1 && pytest -q")
        self.assertEqual(rule, "segments")
        self.assertIn("Write absolute paths", first_line(why))

    def test_sed_line1_names_print_range_form(self):  # C1: sed reason
        rule, why = cmd_guard.verdict_rule("sed -n 's/a/b/p' f.txt")
        self.assertEqual(rule, "sed")
        self.assertIn("`sed -n 'A,Bp' <file>`", first_line(why))


class SilentRewriteTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.scratch = os.path.realpath(t.name)

    def run_hook(self, command):
        out = io.StringIO()
        payload = json.dumps({"tool_input": {"command": command}})
        env = mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: self.scratch})
        with env, mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(out):
            cmd_guard.main()
        return out.getvalue()

    def test_cd_git_is_rewritten_without_deny(self):  # C3: silent rewrite to git -C
        with mock.patch("os.getcwd", return_value=MAIN_CWD):
            out = self.run_hook("cd /x && git status")
        got = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(got["updatedInput"], {"command": "git -C /x status"})
        self.assertNotIn("permissionDecision", got)

    def test_unsafe_dir_stays_a_deny_naming_git_c(self):  # C3 decoy: the rewrite cannot carry `$HOME` over
        with mock.patch("os.getcwd", return_value=MAIN_CWD):
            out = self.run_hook("cd $HOME && git status")
        got = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(got["permissionDecision"], "deny")
        self.assertIn("git -C", first_line(got["permissionDecisionReason"]))


if __name__ == "__main__":
    unittest.main()
