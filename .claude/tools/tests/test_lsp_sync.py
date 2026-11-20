"""Client document sync against the stdlib fake server in tests/support/fake_lsp.py (no real language server needed)."""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import lsp_client
from search.lsp_client import Client, uri
from tests.support.tmpdirs import tracked_mkdtemp

FAKE = str(Path(__file__).parent / "support" / "fake_lsp.py")


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        patcher = mock.patch.dict(lsp_client.SERVERS, {"py": ([sys.executable, "-I", FAKE], "python")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.c = Client("py", self.root)
        self.addCleanup(self._stop, self.c)
        self.sent: list = []
        real = self.c.note
        self.c.note = lambda m, p: (self.sent.append(m), real(m, p))[1]  # record outgoing notifications

    @staticmethod
    def _stop(c):
        c.close()
        c.p.wait()
        assert c.p.poll() is not None

    def write(self, name, text, mtime=None):
        f = os.path.join(self.root, name)
        Path(f).write_text(text)
        if mtime is not None:
            os.utime(f, ns=(mtime, mtime))
        return f

    def hover(self, f):
        return self.c.req("textDocument/hover", {"textDocument": {"uri": uri(f)}, "position": {"line": 0, "character": 0}})["contents"]

    def until(self, cond, secs=10):
        end = time.time() + secs
        while not cond() and time.time() < end:
            self.c.drain()
            time.sleep(0.02)
        return cond()

    def test_open_then_edit_shows_new_version(self):
        f = self.write("a.py", "one\n")
        self.c.sync(f)
        self.assertEqual(self.hover(f), "v1:one")
        self.write("a.py", "two!\n")
        self.c.sync(f)
        self.assertEqual(self.hover(f), "v2:two!")
        self.assertEqual(self.sent.count("textDocument/didChange"), 1)

    def test_touch_without_edit_sends_nothing(self):
        f = self.write("a.py", "one\n")
        self.c.sync(f)
        self.sent.clear()
        os.utime(f, ns=(1, 1))
        self.c.sync(f)  # mtime changed, hash same
        self.c.sync(f)  # stat shortcut
        self.assertEqual(self.sent, [])

    def test_same_size_same_mtime_edit_detected_for_queried_file(self):
        f = self.write("a.py", "one\n", mtime=10**18)
        self.c.sync(f)
        self.write("a.py", "two\n", mtime=10**18)
        self.c.open(f)  # queried file: hash check even when the stat is unchanged
        self.assertEqual(self.hover(f), "v2:two")

    def test_ts_check_flip_resends(self):
        f = self.write("a.js", "x\n")
        self.c.sync(f)
        self.c.sync(f, ts_check=True)
        self.assertEqual(self.hover(f), "v2:// @ts-check")
        self.c.sync(f, ts_check=True)
        self.assertEqual(self.sent.count("textDocument/didChange"), 1)

    def test_change_drops_cached_diagnostics(self):
        f = self.write("a.py", "one\n")
        self.c.sync(f)
        self.assertTrue(self.until(lambda: uri(f) in self.c.diags))
        self.write("a.py", "two!\n")
        self.c.sync(f)
        self.assertNotIn(uri(f), self.c.diags)
        self.assertTrue(self.until(lambda: self.c.diags.get(uri(f), [{}])[0].get("message") == "d2"))

    def test_vanished_file_is_closed_and_forgotten(self):
        f = self.write("a.py", "one\n")
        self.c.sync(f)
        os.remove(f)
        self.c.sync(f)
        self.assertEqual(self.sent.count("textDocument/didClose"), 1)
        self.assertNotIn(uri(f), self.c.docs)
        self.c.sync(f)
        self.assertEqual(self.sent.count("textDocument/didClose"), 1)

    def test_ensure_open_all_closes_vanished(self):
        fs = [self.write(f"f{i}.py", f"t{i}\n") for i in range(3)]
        self.c.ensure_open_all(self.root, "py", skip=())
        self.assertEqual(sorted(self.c.docs), sorted(uri(f) for f in fs))
        os.remove(fs[1])
        self.sent.clear()
        self.c.ensure_open_all(self.root, "py", skip=())
        self.assertEqual(self.sent, ["textDocument/didClose"])
        self.assertNotIn(uri(fs[1]), self.c.docs)
        self.assertEqual(self.hover(fs[0]), "v1:t0")

    def test_ensure_open_all_skips_listed(self):
        f = self.write("a.py", "one\n")
        self.c.ensure_open_all(self.root, "py", skip=(f,))
        self.assertEqual(self.c.docs, {})

    def test_drain_survives_flood(self):
        self.c.note("fake/flood", {"n": 300})  # ~300 KB of notifications, far above the pipe buffer
        self.assertTrue(self.until(lambda: len(self.c.diags) >= 300))
        f = self.write("a.py", "one\n")
        self.c.sync(f)
        self.assertEqual(self.hover(f), "v1:one")

    def test_stale_diagnostics_version_ignored(self):
        f = self.write("a.py", "one\n")
        self.c.sync(f)
        self.write("a.py", "two!\n")
        self.c.sync(f)
        self.c.note("fake/publish", {"uri": uri(f), "version": 1, "message": "stale"})
        self.c.note("fake/publish", {"uri": uri(f), "version": 2, "message": "fresh"})
        self.assertTrue(self.until(lambda: self.c.diags.get(uri(f), [{}])[0].get("message") == "fresh"))

    def test_alive_and_dead_server(self):
        self.assertTrue(self.c.alive())
        self.c.p.kill()
        self.c.p.wait()
        self.assertFalse(self.c.alive())
        with self.assertRaises((EOFError, OSError)):
            self.c.req("textDocument/hover", {}, 2)


if __name__ == "__main__":
    unittest.main()
