"""Warm LSP pool against the stdlib fake server (no real language server needed)."""

import os
import signal
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import lsp_client, lsp_pool
from search.lsp_client import Client
from tests.support.tmpdirs import tracked_mkdtemp

FAKE = str(Path(__file__).parent / "support" / "fake_lsp.py")
real_req = Client.req


def short_req(self, method, params, wait=60):
    if method != "textDocument/hover":  # keep the initialize handshake at its normal wait under load
        return real_req(self, method, params, wait)
    return real_req(self, method, params, min(wait, 0.5))


def wait_dead(pid, within=10):
    """True once PID is gone or a zombie (SIGKILL delivered; nobody may have reaped it yet). Polls up to WITHIN seconds."""
    end = time.monotonic() + within
    while time.monotonic() < end:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                if fh.read().split(")")[-1].split()[0] == "Z":
                    return True
        except OSError:
            return True
        time.sleep(0.02)
    return False


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class PoolBase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(lsp_client.SERVERS, {"py": ([sys.executable, "-I", FAKE], "python")})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.pool = lsp_pool.Pool(interval=3600)
        self.addCleanup(self.pool.close_all)
        self.root = tracked_mkdtemp()
        Path(self.root, "pyproject.toml").write_text("")
        self.f = self.mk("a.py", "first\n")
        self.key = ("py", self.root)

    def mk(self, name, text):
        p = os.path.join(self.root, name)
        Path(p).write_text(text)
        return p

    def hover(self, f=None, key=None):
        return self.pool.run(key or self.key, ["hover", f or self.f, "1"], f or self.f)


class NavTests(PoolBase):
    def test_nav_captures_output(self):
        with mock.patch.object(lsp_pool, "POOL", self.pool):
            out, err, code = lsp_pool.nav(["hover", "a.py", "1"], self.root)
        self.assertEqual((out.strip(), err, code), ("v1:first", "", 0))

    def test_unpooled_op_falls_back(self):
        with self.assertRaises(lsp_pool.Fallback):
            lsp_pool.nav(["symbols", "x"], self.root)

    def test_pids_and_close_all_twice(self):
        self.hover()
        pids = self.pool.pids()
        self.assertEqual(len(pids), 1)
        self.pool.close_all()
        self.pool.close_all()
        self.assertEqual(self.pool.pids(), [])
        self.assertFalse(alive(pids[0]))


class ConcurrencyTests(PoolBase):
    def run_threads(self, calls, target=0):
        """Run CALLS in threads and -> the peak number inside `_once` at once. TARGET > 0: every call is held until TARGET calls are inside (an Event, exact, no sleep). TARGET 0: a short hold gives overlapping calls time to show up (used to prove SERIALISATION: if the pool works the peak is 1 whatever the timing)."""
        seen = {"now": 0, "max": 0}
        lock = threading.Lock()
        reached = threading.Event()
        real_once = self.pool._once

        def once(*a):
            with lock:
                seen["now"] += 1
                seen["max"] = max(seen["max"], seen["now"])
                if target and seen["now"] >= target:
                    reached.set()
            if target:
                reached.wait(10)
            else:
                time.sleep(0.3)
            try:
                return real_once(*a)
            finally:
                with lock:
                    seen["now"] -= 1

        self.pool._once = once
        ts = [threading.Thread(target=c) for c in calls]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        return seen["max"]

    def test_same_key_serialises(self):
        self.assertEqual(self.run_threads([self.hover] * 3), 1)

    def test_different_keys_run_in_parallel(self):
        other = tracked_mkdtemp()
        Path(other, "pyproject.toml").write_text("")
        g = os.path.join(other, "b.py")
        Path(g).write_text("x\n")
        peak = self.run_threads([self.hover, lambda: self.hover(g, ("py", other))], target=2)
        self.assertEqual(peak, 2)

    def test_many_threads_all_answer(self):
        res = []
        ts = [threading.Thread(target=lambda: res.append(self.hover()[2])) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(res, [0] * 8)


class FailureTests(PoolBase):
    def test_dead_client_replaced(self):
        self.hover()
        (pid,) = self.pool.pids()
        os.kill(pid, signal.SIGKILL)
        self.assertTrue(wait_dead(pid))
        self.assertEqual(self.hover()[2], 0)
        self.assertNotIn(pid, self.pool.pids())

    def test_killed_between_alive_check_and_use_retries(self):
        self.hover()
        e = self.pool.entries[self.key]
        old = e.client
        old.alive = lambda: True  # hide the death: the request itself fails, the retry starts a fresh client
        os.kill(old.p.pid, signal.SIGKILL)
        old.p.wait()
        self.assertEqual(self.hover()[2], 0)
        self.assertIsNot(e.client, old)

    def test_silent_server_retries_once_then_fallback(self):
        os.environ["FAKE_SILENT"] = "1"
        spawned = []
        real_init = Client.__init__

        def spy(c, *a):
            real_init(c, *a)
            spawned.append(c)

        with mock.patch.object(Client, "req", short_req), mock.patch.object(Client, "__init__", spy), self.assertRaises(lsp_pool.Fallback):
            self.hover()
        self.assertEqual(len(spawned), 2)  # first try plus exactly one retry
        for c in spawned:
            self.assertIsNotNone(c.p.wait(5))
        self.assertEqual(self.pool.pids(), [])
        self.assertNotIn(self.key, self.pool.entries)

    def test_start_failure_falls_back(self):
        with mock.patch.dict(lsp_client.SERVERS, {"py": (["/nonexistent-lsp-binary"], "python")}), self.assertRaises(lsp_pool.Fallback):
            self.hover()


class LifecycleTests(PoolBase):
    def test_lru_cap(self):
        os.environ["LSP_POOL_MAX"] = "2"
        procs = []
        for _i in range(3):
            d = tracked_mkdtemp()
            Path(d, "pyproject.toml").write_text("")
            g = os.path.join(d, "b.py")
            Path(g).write_text("x\n")
            self.hover(g, ("py", d))
            procs.append(self.pool.entries[("py", d)].client.p)
        procs[0].wait(5)
        self.assertIsNotNone(procs[0].poll())
        self.assertEqual(len(self.pool.pids()), 2)

    def test_idle_reap(self):
        self.hover()
        (pid,) = self.pool.pids()
        self.pool.reap(time.time() + 100)
        self.assertEqual(len(self.pool.pids()), 1)  # default idle is 600 s
        os.environ["LSP_POOL_IDLE"] = "50"
        self.pool.reap(time.time() + 100)
        self.assertEqual(self.pool.pids(), [])
        self.assertEqual(self.hover()[2], 0)  # a new client on demand
        self.assertNotIn(pid, self.pool.pids())

    def test_marker_change_rebuilds(self):
        self.hover()
        old = self.pool.entries[self.key].client
        marker = os.path.join(self.root, "pyproject.toml")
        os.utime(marker, ns=(1_000_000_000, 1_000_000_000))
        self.assertEqual(self.hover()[2], 0)
        self.assertIsNot(self.pool.entries[self.key].client, old)
        self.assertIsNotNone(old.p.wait(5))

    def test_janitor_lazy_and_stops(self):
        self.assertIsNone(self.pool.janitor)
        self.hover()
        j = self.pool.janitor
        assert j is not None
        self.assertTrue(j.is_alive())
        self.pool.close_all()
        self.assertFalse(j.is_alive())

    def test_janitor_reaps_on_interval(self):
        os.environ["LSP_POOL_IDLE"] = "0"
        self.pool.interval = 0.05
        self.hover()
        for _ in range(100):
            if not self.pool.pids():
                break
            time.sleep(0.05)
        self.assertEqual(self.pool.pids(), [])


if __name__ == "__main__":
    unittest.main()
