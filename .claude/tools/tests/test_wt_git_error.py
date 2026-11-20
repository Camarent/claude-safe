"""Synthetic tests: `wt.git` shows a failing hook's stdout, and the cmd_guard hook is registered and denies by JSON."""

import json
import os
import subprocess
import sys
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [TOOLS]

import wt  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402

SETTINGS = os.path.join(os.path.dirname(TOOLS), "settings.json")
GUARD = os.path.join(TOOLS, "cmd_guard.py")


def sh(*a, cwd):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)


class GitError(unittest.TestCase):
    def repo(self):
        d = tracked_mkdtemp()
        sh("git", "init", "-q", cwd=d)
        sh("git", "config", "user.email", "t@example.invalid", cwd=d)
        sh("git", "config", "user.name", "t", cwd=d)
        return d

    def test_hook_stdout_is_in_the_error(self):
        d = self.repo()
        hook = os.path.join(d, ".git", "hooks", "pre-commit")
        with open(hook, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho 'LEAK found: secret in a.txt'\nexit 1\n")
        os.chmod(hook, 0o755)
        with open(os.path.join(d, "a.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        sh("git", "add", "a.txt", cwd=d)
        with self.assertRaises(RuntimeError) as cm:
            wt.git(d, "commit", "-q", "-m", "m")
        self.assertIn("LEAK found: secret in a.txt", str(cm.exception))

    def test_plain_stderr_error_still_shown(self):
        d = self.repo()
        with self.assertRaises(RuntimeError) as cm:
            wt.git(d, "checkout", "no-such-branch")
        self.assertIn("no-such-branch", str(cm.exception))

    def test_check_false_returns_stdout_and_never_raises(self):
        d = self.repo()
        self.assertEqual(wt.git(d, "checkout", "no-such-branch", check=False), "")


def decision(cmd):
    p = subprocess.run([sys.executable, "-I", GUARD], input=json.dumps({"tool_input": {"command": cmd}}), capture_output=True, text=True, check=False)
    return p.returncode, p.stdout.strip()


class GuardRegistered(unittest.TestCase):
    def test_settings_register_the_guard_after_snip(self):
        with open(SETTINGS, encoding="utf-8") as f:
            pre = json.load(f)["hooks"]["PreToolUse"]
        cmds = [h["command"] for e in pre if e.get("matcher") == "Bash" for h in e["hooks"]]
        self.assertTrue(any(c.endswith("snip hook") for c in cmds))
        self.assertTrue(any("cs-cmd-guard" in c for c in cmds))
        self.assertLess(next(i for i, c in enumerate(cmds) if "snip hook" in c), next(i for i, c in enumerate(cmds) if "cs-cmd-guard" in c))
        self.assertTrue(os.path.isfile(GUARD))

    def test_pipe_after_qrun_is_denied(self):
        rc, out = decision("python3 -I /root/.claude/tools/qrun.py -- node --test a.mjs | tail -5")
        self.assertEqual(rc, 0)
        self.assertIn("deny", out)

    def test_var_prefix_on_test_is_denied(self):
        rc, out = decision("FOO=1 node --test x.mjs")
        self.assertEqual(rc, 0)
        self.assertIn("deny", out)

    def test_plain_commands_and_prose_are_allowed(self):
        for cmd in (
            "ls -la | head",
            "git status && git diff --stat",
            "python3 -I /root/.claude/tools/qrun.py -- node --test a.mjs",
            "echo 'qrun.py; x'",
            "FRNUM=1 python3 x.py",
        ):
            self.assertEqual(decision(cmd), (0, ""), cmd)

    def test_bad_input_allows(self):
        p = subprocess.run([sys.executable, "-I", GUARD], input="not json", capture_output=True, text=True, check=False)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, ""))


if __name__ == "__main__":
    unittest.main()
