"""Warm language-server pool: one Client per (lang, root), one lock per entry, LRU cap, idle reaping, retry once then Fallback.

nav(argv, cwd) runs one lsp op (`def|refs|hover|outline|diag FILE SPEC`) and returns (out, err, code). Env: LSP_POOL_MAX (default 6), LSP_POOL_IDLE (seconds, default 600).
"""

import collections
import io
import os
import sys
import threading
import time

from search import codesrv, lsp
from search.lsp_client import EXT, MARKERS, Client, root_of

JANITOR_EVERY = 30.0  # seconds between reap/drain passes
FAILURES = (TimeoutError, EOFError, OSError, ValueError)  # server dead, silent or garbled: retry on a fresh client


class Fallback(Exception):
    """The warm pool cannot answer; the caller runs the one-shot path."""


def _limit(name, default):
    return int(os.environ.get(name, default))


def signature(root):
    """mtime_ns of every marker/config file directly under ROOT (None when absent)."""
    out: list[int | None] = []
    for m in MARKERS:
        try:
            out.append(os.stat(os.path.join(root, m)).st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out)


class Entry:
    def __init__(self):
        self.lock = threading.Lock()
        self.client = None
        self.sig = None
        self.last = time.time()
        self.gone = False  # evicted or reaped: a thread that still holds it must look the key up again

    def close(self):
        c, self.client = self.client, None
        self.gone = True
        if c:
            _kill(c)


def _kill(c):
    try:
        c.close()
    finally:
        c.p.kill()
        c.p.wait()


class Pool:
    def __init__(self, interval=JANITOR_EVERY):
        self.entries = collections.OrderedDict()  # (lang, root) -> Entry, least recently used first
        self.glock = threading.Lock()
        self.interval = interval
        self.stop = threading.Event()
        self.janitor = None

    # lookup and eviction
    def entry(self, key):
        evicted = []
        with self.glock:
            e = self.entries.get(key)
            if e is None:
                e = self.entries[key] = Entry()
            self.entries.move_to_end(key)
            while len(self.entries) > max(1, _limit("LSP_POOL_MAX", 6)):
                evicted.append(self.entries.popitem(last=False)[1])
        for old in evicted:
            self._close_entry(old)
        return e

    @staticmethod
    def _close_entry(e):
        with e.lock:
            e.close()

    def _drop(self, key, e):
        with self.glock:
            if self.entries.get(key) is e:
                del self.entries[key]
        e.gone = True

    # client lifecycle (entry lock held)
    def _ensure(self, key, e):
        lang, root = key
        sig = signature(root)
        if e.client is not None and (not e.client.alive() or e.sig != sig):
            self._discard(e)
        if e.client is None:
            try:
                e.client, e.sig = Client(lang, root), sig
            except (OSError, EOFError, RuntimeError, TimeoutError, KeyError) as exc:
                raise Fallback(f"server failed to start: {exc}") from exc
            self._start_janitor()
        return e.client

    @staticmethod
    def _discard(e):
        c, e.client = e.client, None
        if c:
            _kill(c)

    # one request
    def run(self, key, argv, f):
        for attempt in (1, 2):
            e = self.entry(key)
            with e.lock:
                if e.gone:
                    continue
                try:
                    return self._once(key, e, argv, f)
                except FAILURES as exc:
                    self._discard(e)
                    if attempt == 2:
                        self._drop(key, e)
                        raise Fallback(f"server failed twice: {exc}") from exc
                finally:
                    e.last = time.time()
        raise Fallback("pool entry kept vanishing")

    def _once(self, key, e, argv, f):
        c = self._ensure(key, e)
        c.drain()
        with _capture() as (out, err):
            try:
                code = lsp.COMMANDS[argv[0]](c, argv, f, key[0], key[1])
            except RuntimeError as exc:  # the server answered with an error: report it like lsp.run_op
                code = lsp.not_checked(f, key[0], f"lsp error: {exc}") if argv[0] == "diag" else _say(f"lsp error: {exc}")
        return out.getvalue(), err.getvalue(), code or 0

    # janitor
    def _start_janitor(self):
        if self.janitor and self.janitor.is_alive():
            return
        self.stop = threading.Event()
        self.janitor = threading.Thread(target=self._loop, args=(self.stop,), daemon=True, name="lsp-pool-janitor")
        self.janitor.start()

    def _loop(self, stop):
        while not stop.wait(self.interval):
            self.reap(time.time())
            self.drain()

    def _snapshot(self):
        with self.glock:
            return list(self.entries.items())

    def reap(self, now):
        """Close clients unused for LSP_POOL_IDLE seconds; busy entries are skipped."""
        idle = _limit("LSP_POOL_IDLE", 600)
        for key, e in self._snapshot():
            if e.client is None or now - e.last < idle or not e.lock.acquire(blocking=False):
                continue
            try:
                if now - e.last >= idle:
                    e.close()
                    self._drop(key, e)
            finally:
                e.lock.release()

    def drain(self):
        for _key, e in self._snapshot():
            if e.client is None or not e.lock.acquire(blocking=False):
                continue
            try:
                if e.client is not None:
                    e.client.drain()
            except FAILURES:  # the next request notices the dead client
                pass
            finally:
                e.lock.release()

    # shutdown
    def pids(self):
        return [e.client.p.pid for _k, e in self._snapshot() if e.client is not None and e.client.alive()]

    def close_all(self):
        self.stop.set()
        j, self.janitor = self.janitor, None
        if j and j is not threading.current_thread():
            j.join(5)
        with self.glock:
            entries, self.entries = list(self.entries.values()), collections.OrderedDict()
        for e in entries:
            got = e.lock.acquire(timeout=2)  # a stuck request must not block shutdown (signal/atexit): kill anyway
            try:
                e.close()
            finally:
                if got:
                    e.lock.release()


def _say(msg):
    print(msg)
    return 3


class _capture:
    """Point this thread's stdout/stderr at fresh buffers via codesrv's per-thread Router (never a global swap per call)."""

    def __enter__(self):
        _install_router()
        self.prev = getattr(codesrv.tl, "out", None), getattr(codesrv.tl, "err", None)
        codesrv.tl.out, codesrv.tl.err = io.StringIO(), io.StringIO()
        return codesrv.tl.out, codesrv.tl.err

    def __exit__(self, *exc):
        codesrv.tl.out, codesrv.tl.err = self.prev


_router_lock = threading.Lock()


def _install_router():
    with _router_lock:  # once: a Router already in place (codesrv serving) is kept
        if not isinstance(sys.stdout, codesrv.Router):
            sys.stdout = codesrv.Router("out", sys.stdout)
        if not isinstance(sys.stderr, codesrv.Router):
            sys.stderr = codesrv.Router("err", sys.stderr)


POOL = Pool()


def nav(argv, cwd):
    """Run one op on a pooled client; return (out, err, code). Raises Fallback when the warm path cannot answer."""
    if len(argv) < 2 or argv[0] not in lsp.COMMANDS:
        raise Fallback("not a pooled op")
    f = argv[1] if os.path.isabs(argv[1]) else os.path.join(cwd, argv[1])
    lang = EXT.get(os.path.splitext(f)[1].lower())
    if not lang or not os.path.exists(f):
        raise Fallback("no server or no such file")
    return POOL.run((lang, root_of(f, cwd)), argv, f)


def close_all():
    POOL.close_all()


def pids():
    return POOL.pids()
