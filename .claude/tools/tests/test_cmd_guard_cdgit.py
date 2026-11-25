"""cmd_guard cd-before-git denial: `git -C` segments pass, the suggestion keeps the real segment, the worktree sentence is conditional, and every deny is logged."""

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

WT_SENTENCE = "your cwd is already the job worktree"
MAIN_CWD = "/repo/main"
WT_CWD = "/srv/x/.wt/stage/task-1/repo"


class CdGitTest(unittest.TestCase):
    def test_git_with_its_own_c_is_not_denied(self):  # C1: the old deny refused this
        self.assertEqual(cmd_guard.verdict("cd /wt/x && git -C /wt/y status --short"), "")

    def test_only_the_bare_git_segment_is_denied_in_a_mixed_chain(self):
        why = cmd_guard.verdict("cd /wt/x && git -C /wt/y status && git log")
        self.assertIn("`git -C /wt/x log`", why)

    def test_suggestion_keeps_the_real_segment_quotes(self):  # C2: built from the segment text, not the stripped words
        why = cmd_guard.verdict("cd /wt/x && git log --format='%h %s' -3")
        self.assertIn("`git -C /wt/x log --format='%h %s' -3`", why)

    def test_suggestion_fallback_without_args(self):
        self.assertIn("`git -C /wt/x <args>`", cmd_guard.verdict("cd /wt/x && git"))

    def test_cwd_command_before_git_keeps_cd_in_subshell(self):  # C1: `ls -a` needs the cd, so the cd stays in a subshell
        cmd = "cd /wt/x && ls -a && git status --short | head"
        rewritten = "(cd /wt/x && ls -a) && git -C /wt/x status --short | head"
        self.assertEqual(cmd_guard.rewrite_cd_git(cmd), rewritten)
        self.assertEqual(cmd_guard.verdict(rewritten), "")

    def test_rewrite_stays_denied_for_unsafe_shapes(self):  # C2: no rewrite, so the cd-git deny stands
        for cmd in (
            "cd /wt/x && git commit -F - <<EOF\nmsg\nEOF",  # heredoc
            "cd /wt/x && (git status)",  # existing subshell
            "cd /wt/x && { git status; }",  # existing group
            "cd /wt/x && ls -a && (git status)",  # group after a subshell wrap
            "cd /wt/x && git status &",  # background
            "cd $HOME && git status",  # dollar in the cd target
            "cd ~/x && git status",  # tilde in the cd target
            "cd /wt/*x && git status",  # glob in the cd target
            "cd /wt/x && git status && ls -a",  # cwd command after git cannot be kept
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(cmd_guard.rewrite_cd_git(cmd), "")

    def test_worktree_sentence_only_under_dot_wt(self):  # C2: cwd under .wt/ only
        with mock.patch("os.getcwd", return_value=WT_CWD):
            self.assertIn(WT_SENTENCE, cmd_guard.verdict("cd /wt/x && git status"))
        with mock.patch("os.getcwd", return_value=MAIN_CWD):
            why = cmd_guard.verdict("cd /wt/x && git status")
            self.assertTrue(why)
            self.assertNotIn(WT_SENTENCE, why)
            self.assertIn("`git -C /wt/x status`", why)


class DenialLogTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.scratch = os.path.realpath(t.name)
        self.log = os.path.join(self.scratch, "denials.jsonl")

    def run_hook(self, command):
        out = io.StringIO()
        payload = json.dumps({"tool_input": {"command": command}})
        env = mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: self.scratch})
        with env, mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(out):
            cmd_guard.main()
        return out.getvalue()

    def rows(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_each_deny_appends_one_row_with_its_rule(self):  # C3
        out = self.run_hook("sed -n 's/a/b/p' f")
        self.assertIn('"permissionDecision": "deny"', out)
        rows = self.rows()
        self.assertEqual([r["rule"] for r in rows], ["sed"])
        self.assertEqual(rows[0]["command"], "sed -n 's/a/b/p' f")

    def test_cd_git_deny_logged_under_cd_git(self):  # a redirect cannot be rewritten, so the cd-git deny stands
        with mock.patch("os.getcwd", return_value=MAIN_CWD):
            out = self.run_hook("cd /wt/x && git status > out.txt")
        self.assertIn('"permissionDecision": "deny"', out)
        self.assertEqual([r["rule"] for r in self.rows()], ["cd_git"])

    def test_allowed_command_logs_nothing(self):
        self.assertEqual(self.run_hook("git status"), "")
        self.assertFalse(os.path.exists(self.log))

    def test_rewritten_cd_git_is_not_a_deny(self):  # a rewrite replaces the deny: nothing to log
        out = self.run_hook("cd /wt/x && git status --short")
        self.assertIn("updatedInput", out)
        self.assertFalse(os.path.exists(self.log))


if __name__ == "__main__":
    unittest.main()
