"""Language-server client for search/lsp.py: server table, URI/path helpers, poll() and the one-shot JSON-RPC Client."""

import hashlib
import json
import os
import select
import subprocess
import time
from typing import IO, cast
from urllib.parse import quote, unquote, urlparse

from pipeline.util import swallowed

SERVERS = {
    "py": (["pyright-langserver", "--stdio"], "python"),
    "js": (["tsc", "--lsp", "--stdio"], "javascript"),
    "ts": (["tsc", "--lsp", "--stdio"], "typescript"),
    "cpp": (["clangd", "--background-index=false"], "cpp"),
    "rs": (["rust-analyzer"], "rust"),
}
EXT = {
    ".py": "py",
    ".js": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".jsx": "js",
    ".ts": "ts",
    ".tsx": "ts",
    ".c": "cpp",
    ".h": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cxx": "cpp",
    ".rs": "rs",
}
MARKERS = (".git", "pyrightconfig.json", "pyproject.toml", "jsconfig.json", "tsconfig.json", "package.json", "compile_commands.json", "Cargo.toml")


def uri(p):
    return "file://" + quote(os.path.abspath(p))


def path(u):
    return unquote(urlparse(u).path)


def root_of(f, cwd=None):
    d = os.path.dirname(os.path.abspath(f))
    while d != "/":
        if any(os.path.exists(os.path.join(d, m)) for m in MARKERS):
            return d
        d = os.path.dirname(d)
    return cwd or os.getcwd()


def sources(root, lang, limit=500):
    exts = tuple(e for e, language in EXT.items() if language == lang or (lang in ("js", "ts") and language in ("js", "ts")))
    out = []
    for d, ds, ns in os.walk(root):
        ds[:] = [x for x in ds if x not in ("node_modules", ".git", "target", "dist", "build", "__pycache__", ".venv")]
        out += [os.path.join(d, n) for n in ns if n.endswith(exts)]
        if len(out) >= limit:
            break
    return out[:limit]


def poll(fn, enough, timeout, interval=0.25):
    """Call FN until enough(result) or TIMEOUT seconds pass; return the longest result seen (never longer total wait than TIMEOUT)."""
    deadline, best = time.time() + timeout, fn()
    while not enough(best) and time.time() < deadline:
        time.sleep(min(interval, max(0, deadline - time.time())))
        r = fn()
        if len(r) >= len(best):
            best = r
    return best


class Client:
    def __init__(self, lang, root):
        cmd, self.lang_id = SERVERS[lang]
        self.root, self.n, self.diags, self.busy, self.buf = root, 0, {}, set(), b""
        self.docs = {}  # uri -> {version, key (mtime_ns, size), ts, hash}: what the server holds
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=root)
        try:
            self.req(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": uri(root),
                    "workspaceFolders": [{"uri": uri(root), "name": "p"}],
                    "capabilities": {
                        "textDocument": {"documentSymbol": {"hierarchicalDocumentSymbolSupport": False}, "publishDiagnostics": {}},
                        "workspace": {"workspaceFolders": True, "configuration": True},
                        "window": {"workDoneProgress": True},
                    },
                },
            )
        except BaseException:  # KEEP: not a swallow; a refused or silent initialize must not leave the server running, then re-raise
            self.p.kill()
            self.p.wait()
            raise
        self.note("initialized", {})

    def send(self, m):
        b = json.dumps(m).encode()
        stdin = cast(IO[bytes], self.p.stdin)  # Popen(stdin=PIPE)
        stdin.write(b"Content-Length: %d\r\n\r\n" % len(b) + b)
        stdin.flush()

    def _fill(self, deadline):  # one more chunk from the server; TimeoutError once DEADLINE passes (None: wait)
        stdout = cast(IO[bytes], self.p.stdout)  # Popen(stdout=PIPE)
        if deadline is not None:
            left = deadline - time.time()
            if left <= 0 or not select.select([stdout], [], [], left)[0]:
                raise TimeoutError("lsp server silent")
        chunk = os.read(stdout.fileno(), 65536)
        if not chunk:
            raise EOFError("server exited")
        self.buf += chunk

    def read(self, deadline=None):
        n = 0
        while True:
            while b"\n" not in self.buf:
                self._fill(deadline)
            line, _, self.buf = self.buf.partition(b"\n")
            line += b"\n"
            if line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":")[1])
        while len(self.buf) < n:
            self._fill(deadline)
        body, self.buf = self.buf[:n], self.buf[n:]
        return json.loads(body)

    @staticmethod
    def config(params):  # pyright searches only open files unless told to index the workspace
        analysis = {"diagnosticMode": "workspace", "useLibraryCodeForTypes": True, "autoSearchPaths": True}
        out = []
        for i in params.get("items", []):
            sec = i.get("section") or ""
            out.append({"analysis": analysis} if sec == "python" else analysis if sec == "python.analysis" else {})
        return out

    @staticmethod
    def _msg(method, params, rid=None):  # JSON-RPC: a call with no params omits the field (shutdown/exit reject "params": null)
        m = {"jsonrpc": "2.0", "method": method}
        if rid is not None:
            m["id"] = rid
        if params is not None:
            m["params"] = params
        return m

    def note(self, method, params):
        self.send(self._msg(method, params))

    def req(self, method, params, wait=60):
        self.n += 1
        rid = self.n
        self.send(self._msg(method, params, rid))
        end = time.time() + wait
        while time.time() < end:
            m = self.read(end)
            if "method" in m and "id" in m:  # server -> client request: answer with an empty result
                self.send({"jsonrpc": "2.0", "id": m["id"], "result": self.config(m["params"]) if m["method"] == "workspace/configuration" else None})
            elif m.get("method") == "textDocument/publishDiagnostics":
                self.store(m["params"])
            elif m.get("method") == "$/progress":
                self.track(m["params"])
            elif m.get("id") == rid:
                if "error" in m:
                    raise RuntimeError(m["error"].get("message", "lsp error"))
                return m.get("result")
        raise TimeoutError(method)

    def store(self, params):  # cache published diagnostics; drop ones for an older document version than we sent
        d = self.docs.get(params["uri"])
        if params.get("version") is not None and d and params["version"] < d["version"]:
            return
        self.diags[params["uri"]] = params["diagnostics"]

    def sync(self, f, ts_check=False, force=False):
        """Bring the server's copy of F up to date: didOpen, didChange (full text) or didClose. FORCE re-reads even when (mtime_ns, size) match."""
        u, d = uri(f), self.docs.get(uri(f))
        try:
            st = os.stat(f)
        except OSError:
            if d:
                self.note("textDocument/didClose", {"textDocument": {"uri": u}})
                del self.docs[u]
                self.diags.pop(u, None)
            return
        key = (st.st_mtime_ns, st.st_size)
        if d and not force and d["key"] == key and d["ts"] == ts_check:
            return
        with open(f, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if ts_check:
            text = "// @ts-check\n" + text  # in memory only; lines shift by 1, undone on print: JS gets semantic checks without a tsconfig
        h = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()
        if not d:
            self.docs[u] = {"version": 1, "key": key, "ts": ts_check, "hash": h}
            self.note("textDocument/didOpen", {"textDocument": {"uri": u, "languageId": self.lang_id, "version": 1, "text": text}})
            return
        d["key"] = key
        if h == d["hash"]:
            return
        d.update(version=d["version"] + 1, ts=ts_check, hash=h)
        self.diags.pop(u, None)
        self.note("textDocument/didChange", {"textDocument": {"uri": u, "version": d["version"]}, "contentChanges": [{"text": text}]})

    def open(self, f, ts_check=False):  # the queried file: always verify by content hash
        self.sync(f, ts_check, force=True)

    def ensure_open_all(self, root, lang, skip=()):
        """Sync every source file under ROOT (stat shortcut) except SKIP, and didClose documents that vanished."""
        for f in sources(root, lang):
            if f not in skip:
                self.sync(f)
        for u in [u for u in self.docs if not os.path.exists(path(u))]:
            self.sync(path(u))

    def alive(self):
        return self.p.poll() is None

    def drain(self):
        """Handle every server message already pending, without blocking: answer requests, store diagnostics, track progress."""
        stdout = cast(IO[bytes], self.p.stdout)
        while select.select([stdout], [], [], 0)[0]:
            chunk = os.read(stdout.fileno(), 65536)
            if not chunk:
                break
            self.buf += chunk
        while (m := self._take()) is not None:
            if "method" in m and "id" in m:
                self.send({"jsonrpc": "2.0", "id": m["id"], "result": self.config(m["params"]) if m["method"] == "workspace/configuration" else None})
            elif m.get("method") == "textDocument/publishDiagnostics":
                self.store(m["params"])
            elif m.get("method") == "$/progress":
                self.track(m["params"])

    def _take(self):  # one complete message from the buffer, else None (buffer untouched)
        head, sep, rest = self.buf.partition(b"\r\n\r\n")
        if not sep:
            return None
        n = next((int(x.split(b":")[1]) for x in head.split(b"\r\n") if x.lower().startswith(b"content-length:")), 0)
        if len(rest) < n:
            return None
        self.buf = rest[n:]
        return json.loads(rest[:n])

    def track(self, p):  # server work-done progress (rust-analyzer loading / cargo check): tokens in flight
        kind = (p.get("value") or {}).get("kind")
        if kind == "begin":
            self.busy.add(str(p.get("token")))
        elif kind == "end":
            self.busy.discard(str(p.get("token")))

    def settle(self, secs, target=None):  # pump messages until diagnostics for `target` arrive (or secs pass)
        end = time.time() + secs
        quiet = None
        while time.time() < end:
            if not self.buf and not select.select([cast(IO[bytes], self.p.stdout)], [], [], 0.25)[0]:
                if quiet and time.time() > quiet and not self.busy:
                    break  # diagnostics seen, then silence: done
                continue
            m = self.read()
            if "method" in m and "id" in m:
                self.send({"jsonrpc": "2.0", "id": m["id"], "result": self.config(m["params"]) if m["method"] == "workspace/configuration" else None})
            elif m.get("method") == "textDocument/publishDiagnostics":
                self.store(m["params"])
                if m["params"]["uri"] == target:
                    quiet = time.time() + 2.0  # later (semantic) pass may follow the syntactic one
            elif m.get("method") == "$/progress":
                self.track(m["params"])
                if quiet:
                    quiet = time.time() + 2.0
        return target in self.diags

    def close(self):
        try:
            self.req("shutdown", None, 5)
            self.note("exit", None)
        except Exception as ex:  # fail open on purpose; swallowed() logs it
            swallowed("lsp_client.close", ex)
        self.p.kill()
