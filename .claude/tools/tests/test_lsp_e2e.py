"""End-to-end warm nav: `local.py nav` through a real codesrv subprocess on real language servers (pyright, tsc, clangd, rust-analyzer).
Each language class skips on a host without its server (the container image has all of them)."""

import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import SHORT_SOCK_BASE, tracked_mkdtemp

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(TOOLS, "search", "local.py")
CODESRV = os.path.join(TOOLS, "search", "codesrv.py")
CALL_TIMEOUT_S = 120  # rust-analyzer loads slowly on the first call
SYMBOL = "helper"
SOCK_PATH_MAX = 100  # AF_UNIX sun_path is 108 bytes; keep a margin below it


def skip_reason(tool):
    """The reason a class skips when `tool` is missing: it names the server or binary."""
    return f"container only: {tool} not installed or not runnable"


def need(tool):
    """Skip a test on a host without the language server or runtime the container image carries."""
    skip = unittest.skipUnless(_usable(tool), skip_reason(tool))

    def mark(cls):
        cls.SERVER = tool  # recorded so a test can check the decorator names the right server
        return skip(cls)

    return mark


def _usable(tool):
    path = shutil.which(tool)
    if not path:
        return False
    try:
        return subprocess.run([path, "--version"], capture_output=True, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def env_pids(sock):
    """Live pids whose environment carries our CODESRV_SOCK: codesrv, its language servers, a restarted codesrv."""
    tag = f"CODESRV_SOCK={sock}".encode()
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            with open(f"/proc/{name}/environ", "rb") as f:
                hit = tag in f.read().split(b"\0")
        except OSError:
            continue
        if hit and alive(int(name)):
            found.append(int(name))
    return found


def descendants(pid, depth=None):
    """Live descendants of pid (depth 1 = children only)."""
    kids: dict = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(name))
    out, todo = [], [(pid, 1)]
    while todo:
        parent, level = todo.pop()
        for k in kids.get(parent, []):
            if alive(k):
                out.append(k)
                if depth is None or level < depth:
                    todo.append((k, level + 1))
    return out


def wait_until(cond, secs):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


class Server:
    """A codesrv subprocess in its own session, on a private socket. Cleanup kills every process that carries its socket name."""

    def __init__(self, tc, idle=1800):
        self.dir = tracked_mkdtemp(prefix="e2e-")
        self.sock = os.path.join(self.dir, "s.sock")
        if len(self.sock.encode()) > SOCK_PATH_MAX:  # long TMPDIR: bind the socket in a short dir, removed with the test
            self.sock = os.path.join(tracked_mkdtemp(prefix="e2e-s-", dir=SHORT_SOCK_BASE), "s.sock")
        self.env = {
            **os.environ,
            "CODESRV_SOCK": self.sock,
            "CODESRV_IDLE": str(idle),
            "ASK_CODE_PY": sys.executable,
            "HOME": os.environ.get("HOME", self.dir),
            "TMPDIR": self.dir,
        }  # servers killed by SIGKILL leave scratch files: keep them in our dir
        self.env.pop("LOCAL_NODAEMON", None)
        self.log = open(os.path.join(self.dir, "codesrv.log"), "wb")  # closed in cleanup
        self.p = subprocess.Popen(
            [sys.executable, "-I", CODESRV], env=self.env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log, start_new_session=True
        )
        tc.addCleanup(self.cleanup)
        if not wait_until(self.up, 30):
            tc.fail("codesrv did not come up")

    def up(self):
        s = socket.socket(socket.AF_UNIX)
        try:
            s.connect(self.sock)
            return True
        except OSError:
            return False
        finally:
            s.close()

    def cleanup(self):
        for pid in env_pids(self.sock):
            with contextlib.suppress(OSError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        self.p.wait(10)
        self.log.close()
        wait_until(lambda: not env_pids(self.sock), 10)

    def call(self, cwd, *args):
        return subprocess.run([sys.executable, "-I", LOCAL, *args], cwd=cwd, env=self.env, capture_output=True, text=True, timeout=CALL_TIMEOUT_S, check=False)

    def lang_pids(self, depth=None):
        return descendants(self.p.pid, depth)


def first_line_no(out):
    m = re.search(r":(\d+):\d+", out)
    return int(m.group(1)) if m else None


def write_tree(root, files):
    for rel, text in files.items():
        p = Path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


if TYPE_CHECKING:

    class _Mixin(unittest.TestCase):
        pass

else:

    class _Mixin:
        pass


class Base(_Mixin):
    """Shared cases; a subclass sets TARGET (file with `helper` defined on line 1) and make(root) -> files."""

    TARGET = ""
    DEF_LINE = 1

    def files(self, root):
        raise NotImplementedError

    def setUp(self):
        self.root = tracked_mkdtemp(prefix="e2e-proj-")
        write_tree(self.root, self.files(self.root))
        self.srv = Server(self)

    def nav(self, op):
        r = self.srv.call(self.root, "nav", op, self.TARGET, *([SYMBOL] if op != "outline" else []))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def edit(self, old, new):
        p = Path(self.root, self.TARGET)
        p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def test_edit_then_nav_without_restart(self):
        self.assertEqual(first_line_no(self.nav("def")), self.DEF_LINE)
        self.assertIn(SYMBOL, self.nav("outline"))
        servers = self.srv.lang_pids(1)  # rust-analyzer spawns short-lived helpers below it
        self.assertTrue(servers, "no warm language server child after the first call")
        text = Path(self.root, self.TARGET).read_text(encoding="utf-8")
        Path(self.root, self.TARGET).write_text((self.COMMENT + " pad\n") * 2 + text, encoding="utf-8")
        self.assertEqual(first_line_no(self.nav("def")), self.DEF_LINE + 2)
        self.edit(SYMBOL, SYMBOL + "Renamed")
        self.assertIn(SYMBOL + "Renamed", self.nav("outline"))
        self.assertTrue(set(servers) <= set(self.srv.lang_pids(1)), "language server restarted between calls")

    COMMENT = "//"

    def test_recovers_after_server_kills(self):
        want = self.nav("def")
        for pid in self.srv.lang_pids():
            with contextlib.suppress(ProcessLookupError):  # a short-lived helper may exit before the kill
                os.kill(pid, signal.SIGKILL)
        self.assertEqual(self.nav("def"), want)
        os.kill(self.srv.p.pid, signal.SIGKILL)
        self.srv.p.wait(10)
        self.assertEqual(self.nav("def"), want)

    def test_parallel_nav_and_find(self):
        want_nav = self.nav("def")
        find_args = ("find", "--no-model", SYMBOL)
        want_find = self.srv.call(self.root, *find_args)
        self.assertEqual(want_find.returncode, 0, want_find.stderr)

        def one(i):
            if i % 2:
                return "nav", self.srv.call(self.root, "nav", "def", self.TARGET, SYMBOL)
            return "find", self.srv.call(self.root, *find_args)

        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(one, range(8)))
        for kind, r in results:
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, want_nav if kind == "nav" else want_find.stdout)

    def test_idle_exit_leaves_no_language_server(self):
        self.srv.cleanup()
        self.srv = Server(self, idle=5)  # long enough that a loaded test thread still sees the warm server
        self.nav("def")
        warm = self.srv.lang_pids()
        self.assertTrue(warm)
        self.assertTrue(wait_until(lambda: not alive(self.srv.p.pid), 60), "codesrv did not exit when idle")
        self.assertTrue(wait_until(lambda: not env_pids(self.srv.sock), 15), "a process survived the idle exit")


@need("pyright-langserver")
class PyE2E(Base, unittest.TestCase):
    TARGET = "lib.py"
    COMMENT = "#"

    def files(self, root):
        return {
            "pyproject.toml": "[project]\nname='t'\n",
            "lib.py": f"def {SYMBOL}():\n    return 1\n",
            "app.py": f"from lib import {SYMBOL}\nprint({SYMBOL}())\n",
        }


@need("tsc")
class TsE2E(Base, unittest.TestCase):
    TARGET = "src/lib.ts"

    def files(self, root):
        return {
            "package.json": '{"name": "t", "type": "module"}\n',
            "tsconfig.json": '{"compilerOptions": {"target": "es2022", "module": "esnext", "moduleResolution": "bundler", "strict": true, "allowJs": true}, "include": ["src"]}\n',
            "src/lib.ts": f"export function {SYMBOL}(): number {{\n  return 1;\n}}\n",
            "src/app.ts": f"import {{ {SYMBOL} }} from './lib';\nconsole.log({SYMBOL}());\n",
            "src/legacy.js": "export function old() { return 2; }\n",
        }


@need("clangd")
class CppE2E(Base, unittest.TestCase):
    TARGET = "lib.cpp"

    def files(self, root):
        cc = [{"directory": root, "command": f"c++ -c {n}", "file": os.path.join(root, n)} for n in ("lib.cpp", "main.cpp")]
        return {
            "compile_commands.json": json.dumps(cc),
            "lib.cpp": f"int {SYMBOL}() {{ return 1; }}\n",
            "main.cpp": f"int {SYMBOL}();\nint main() {{ return {SYMBOL}(); }}\n",
        }


@need("rust-analyzer")
class RustE2E(Base, unittest.TestCase):
    TARGET = "src/main.rs"

    def files(self, root):
        return {
            "Cargo.toml": '[package]\nname = "t"\nversion = "0.1.0"\nedition = "2021"\n',
            "src/main.rs": f"fn {SYMBOL}() -> i32 {{ 1 }}\nfn main() {{ {SYMBOL}(); }}\n",
        }


class SkipReasonTest(unittest.TestCase):
    """Every skip of the language classes carries a non-empty reason naming its server or binary."""

    def test_every_language_class_skip_reason_names_its_server(self):
        expected = {PyE2E: "pyright-langserver", TsE2E: "tsc", CppE2E: "clangd", RustE2E: "rust-analyzer"}
        for cls, tool in expected.items():
            server = vars(cls)["SERVER"]  # set by need(); Base declares no SERVER
            self.assertEqual(server, tool, cls.__name__)
            if not getattr(cls, "__unittest_skip__", False):
                continue  # server present on this host: the class runs, no skip reason to check
            reason = getattr(cls, "__unittest_skip_why__", "")  # the reason unittest prints for this class
            self.assertTrue(reason.strip(), cls.__name__)
            self.assertIn(tool, reason)

    def test_need_marks_missing_server_with_its_reason(self):
        class Probe:
            pass

        missing = "no-such-language-server-xyz"
        need(missing)(Probe)
        self.assertTrue(getattr(Probe, "__unittest_skip__", False))
        why = getattr(Probe, "__unittest_skip_why__", "")
        self.assertEqual(why, skip_reason(missing))
        self.assertIn(missing, why)


if __name__ == "__main__":
    unittest.main()
