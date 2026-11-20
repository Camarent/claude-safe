"""Command index: paths.json, the cs-* wrappers (in sync, runnable, exit 2 on a missing target) and root swapping."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOOLS)
from pipeline import cmdindex as C  # noqa: E402
from pipeline.configuration import HOME  # noqa: E402

HELP_TIMEOUT = 30
INDEX = C.load()


def run_wrapper(path, cwd, *args):
    return subprocess.run([path, *args], check=False, cwd=cwd, capture_output=True, text=True, timeout=HELP_TIMEOUT, stdin=subprocess.DEVNULL)


class TestIndex(unittest.TestCase):
    def test_every_script_exists(self):
        for key in INDEX["commands"]:
            self.assertTrue(os.path.isfile(C.resolve(INDEX, key)), key)

    def test_wrappers_in_sync(self):
        self.assertEqual(C.gen_bin(HOME, check=True), [])

    def test_host_only_not_in_container_bin(self):
        host = [C.command_name(INDEX, k) for k, e in INDEX["commands"].items() if C.side_of(e) == "host"]
        self.assertGreaterEqual(len(host), 6)
        self.assertEqual(set(host) & set(os.listdir(os.path.join(HOME, C.BIN_DIRS["container"]))), set())
        self.assertEqual(set(C.commands(INDEX, "container")) & {"run", "dev", "release"}, set())
        self.assertTrue(set(host) <= set(os.listdir(os.path.join(HOME, C.BIN_DIRS["host"]))))

    def test_search_tool_names_come_from_index(self):
        from pipeline.configuration import SEARCH_TOOL_NAMES

        self.assertEqual(SEARCH_TOOL_NAMES, tuple(dict.fromkeys(C.names_for(INDEX, "lp"))))

    def test_lp_keeps_name_and_aliases(self):
        self.assertEqual(C.command_name(INDEX, "lp"), "lp")
        self.assertIn("local.py", C.names_for(INDEX, "lp"))
        self.assertEqual(C.names_for(INDEX, "qrun"), ["cs-qrun", "qrun.py"])


class TestWrappers(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cwd, True)

    def test_each_container_wrapper_runs_target(self):
        for key, entry in C.commands(INDEX, "container").items():
            if shutil.which(entry.get("runner", C.DEFAULT_RUNNER).split()[0]) is None:
                continue  # runner not installed here (node on a bare host)
            path = os.path.join(HOME, C.BIN_DIRS["container"], C.command_name(INDEX, key))
            r = run_wrapper(path, self.cwd, "--help")
            self.assertNotIn("target missing", r.stderr, key)
            self.assertNotEqual(r.returncode, 127, key)

    def test_missing_target_exits_2(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        shutil.copy(os.path.join(HOME, C.INDEX_NAME), home)
        self.assertEqual(C.gen_bin(home) != [], True)
        r = run_wrapper(os.path.join(home, C.BIN_DIRS["container"], "cs-qrun"), self.cwd, "--help")
        self.assertEqual(r.returncode, C.EXIT_MISSING)
        self.assertIn("target missing", r.stderr)

    def test_check_detects_deleted_wrapper(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        shutil.copy(os.path.join(HOME, C.INDEX_NAME), home)
        C.gen_bin(home)
        os.remove(os.path.join(home, C.BIN_DIRS["container"], "cs-wave"))
        self.assertEqual(len(C.gen_bin(home, check=True)), 1)

    def test_args_pass_through(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        shutil.copy(os.path.join(HOME, C.INDEX_NAME), home)
        os.makedirs(os.path.join(home, "tools"))
        with open(os.path.join(home, "tools", "qrun.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nprint('|'.join(sys.argv[1:]))\n")
        C.gen_bin(home)
        r = run_wrapper(os.path.join(home, C.BIN_DIRS["container"], "cs-qrun"), self.cwd, "a b", "c")
        self.assertEqual(r.stdout.strip(), "a b|c")


class TestSwap(unittest.TestCase):
    def test_container_roots_map_to_host(self):
        text, n = C.swap("see /root/.claude/practices/x.md and /workspace/y", "host", INDEX, "/h/.claude")
        self.assertEqual(text, f"see /h/.claude/practices/x.md and {os.path.expanduser(INDEX['roots']['projects_dir']['host'])}/y")
        self.assertEqual(n, 2)

    def test_container_side_is_identity(self):
        self.assertEqual(C.swap("/root/.claude/x", "container", INDEX), ("/root/.claude/x", 0))


class TestCheckPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.write("paths.json", '{"prefix": "cs-", "roots": {}, "commands": {"a": {"script": "a.py"}, "b": {"script": "b.py"}}}')
        self.write(
            "settings.json", '{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "cs-a --x"}, {"type": "command", "command": "cs-gone y"}]}]}}'
        )
        self.exe("cs-a")

    def write(self, name, text):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as f:
            f.write(text)

    def exe(self, name):
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        os.chmod(path, C.WRAPPER_MODE)

    def run_check(self, strict):
        env = {**os.environ, C.STRICT_ENV: "1" if strict else "0"}
        return subprocess.run(
            [sys.executable, "-I", os.path.join(TOOLS, "cmdindex.py"), "check-path", "--settings", os.path.join(self.tmp, "settings.json"), "--bin", self.bin, "--home", self.tmp],
            check=False, capture_output=True, text=True, timeout=HELP_TIMEOUT, env=env, cwd=TOOLS,
        )  # fmt: skip

    def test_warns_on_unresolved_hook_and_index_name(self):
        r = self.run_check(False)
        self.assertEqual(r.returncode, 0)
        self.assertIn("not on PATH: hook: cs-gone", r.stderr)
        self.assertIn("not on PATH: index: cs-b", r.stderr)
        self.assertNotIn("cs-a\n", r.stderr)

    def test_strict_exits_nonzero(self):
        self.assertEqual(self.run_check(True).returncode, C.EXIT_UNRESOLVED)

    def test_clean_is_silent(self):
        self.exe("cs-gone")
        self.exe("cs-b")
        r = self.run_check(True)
        self.assertEqual((r.returncode, r.stderr), (0, ""))


class TestRootExpand(unittest.TestCase):
    def test_host_value_expands_tilde_and_env(self):
        from unittest import mock

        fake_home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fake_home, True)
        index = {"roots": {"ws": {"container": "/workspace", "host": "~/workspace"}, "ev": {"host": "$HOME/other"}}}
        with mock.patch.dict(os.environ, {"HOME": fake_home}):
            self.assertEqual(C.root(index, "ws", "host"), os.path.join(fake_home, "workspace"))
            self.assertEqual(C.root(index, "ev", "host"), os.path.join(fake_home, "other"))
            self.assertEqual(C.root(index, "ws", "container"), "/workspace")


if __name__ == "__main__":
    unittest.main()
