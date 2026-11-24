"""Hooks in settings.json call guards by cs-* name (paths.json), and a missing wrapper blocks (exit 2) instead of failing open, except the write guard, which fails open."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLAUDE = os.path.abspath(os.path.join(TOOLS, ".."))
sys.path.insert(0, TOOLS)
from pipeline import cmdindex as C  # noqa: E402

GUARDS = ("cmd-guard", "review-guard", "write-guard", "model-guard")
# Guards that fail open when their binary is missing (settings.json `exit 0`), on purpose: a missing write guard blocked every Edit and Write.
FAIL_OPEN_GUARDS = ("write-guard",)
BLOCKED = 2
OK = 0
SH = "/bin/sh"


def hook_commands():
    with open(os.path.join(CLAUDE, "settings.json"), encoding="utf-8") as f:
        return C.hook_commands(json.load(f))


def cs_tokens(cmd):
    """Words of a hook command that name a cs-* command."""
    return [w for w in cmd.replace("&&", " ").replace("||", " ").split() if w.startswith(C.load()["prefix"])]


class HookCommandsTest(unittest.TestCase):
    def test_guards_are_hooks_by_name(self):
        names = {w for c in hook_commands() for w in cs_tokens(c)}
        index = C.load()
        for key in GUARDS:
            self.assertIn(C.command_name(index, key), names, f"{key} hook missing")

    def test_every_cs_name_is_an_index_command(self):
        index = C.load()
        known = {n for k in index["commands"] for n in C.names_for(index, k)}
        for cmd in hook_commands():
            for w in cs_tokens(cmd):
                self.assertIn(w, known, f"hook names unknown command {w}")

    def test_no_container_path_in_hooks(self):
        for cmd in hook_commands():
            self.assertNotIn("/root/.claude/tools", cmd)

    def test_missing_wrapper_blocks(self):
        empty = tempfile.mkdtemp(prefix="hookpath-")
        self.addCleanup(shutil.rmtree, empty, True)
        index = C.load()
        fail_open = {C.command_name(index, key) for key in FAIL_OPEN_GUARDS}
        for cmd in hook_commands():
            if not cs_tokens(cmd) or fail_open & set(cs_tokens(cmd)):
                continue
            done = subprocess.run([SH, "-c", cmd], check=False, env={"PATH": empty}, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            # cs-write-guard fails open by design (fix(hooks): a missing write guard must not block every Edit)
            want = OK if "cs-write-guard" in cs_tokens(cmd) else BLOCKED
            self.assertEqual(done.returncode, want, f"{cmd!r}: {done.stderr}")


if __name__ == "__main__":
    unittest.main()
