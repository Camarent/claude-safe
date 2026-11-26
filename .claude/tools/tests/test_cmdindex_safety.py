"""cmdindex safety: swap is single-pass, root() stops on cycles, gen-bin keeps hand-made files and writes wrappers atomically."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOOLS)
from pipeline import cmdindex as C  # noqa: E402
from pipeline.configuration import HOME  # noqa: E402

HOST_HOME = "/srv/user/workspace/claude-safe/.claude"
ROOTS_INDEX = {
    "roots": {
        "claude": {"container": "/root/.claude", "host": "@home"},
        "ws": {"container": "/workspace", "host": "/srv/user/workspace"},
    },
    "commands": {},
    "prefix": "cs-",
}


class SwapTest(unittest.TestCase):
    def test_second_pair_does_not_rewrite_host_value(self):
        # host home holds "/workspace"; a second pass must not replace it again
        text, n = C.swap("run /root/.claude/tools/qrun.py in /workspace/proj", "host", ROOTS_INDEX, HOST_HOME)
        self.assertEqual(text, f"run {HOST_HOME}/tools/qrun.py in /srv/user/workspace/proj")
        self.assertEqual(n, 2)


class RootCycleTest(unittest.TestCase):
    def test_cyclic_refs_return_none(self):
        index = {"roots": {"a": {"host": "@b"}, "b": {"host": "@a"}}}
        self.assertIsNone(C.root(index, "a", "host"))


class GenBinTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        shutil.copy(os.path.join(HOME, C.INDEX_NAME), self.home)
        self.bin = os.path.join(self.home, C.BIN_DIRS["container"])
        os.makedirs(self.bin)

    def test_hand_made_file_and_subdir_survive_stale_cleanup(self):
        with open(os.path.join(self.bin, "my-own-script"), "w", encoding="utf-8") as f:
            f.write("echo mine\n")
        os.makedirs(os.path.join(self.bin, "subdir"))
        C.gen_bin(self.home)
        self.assertTrue(os.path.isfile(os.path.join(self.bin, "my-own-script")))
        self.assertTrue(os.path.isdir(os.path.join(self.bin, "subdir")))

    def test_generated_stale_wrapper_is_removed(self):
        stale = os.path.join(self.bin, "cs-old")
        with open(stale, "w", encoding="utf-8") as f:
            f.write(f"#!/bin/sh\n{C.GENERATED_NOTE}\n")
        C.gen_bin(self.home)
        self.assertFalse(os.path.exists(stale))

    def test_wrapper_written_by_atomic_replace(self):
        with mock.patch("pipeline.cmdindex.os.replace", wraps=os.replace) as rep:
            C.gen_bin(self.home)
        self.assertTrue(rep.called)
        for call in rep.call_args_list:
            self.assertNotEqual(os.path.dirname(call.args[0]), "")
            self.assertEqual(os.path.dirname(call.args[0]), os.path.dirname(call.args[1]))
        self.assertEqual([f for f in os.listdir(self.bin) if f.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
