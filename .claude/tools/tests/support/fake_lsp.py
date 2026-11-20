"""Stdlib fake LSP server for document-sync tests: stores text+version per uri, answers hover with 'v<version>:<first line>'.

Extra notifications for tests: fake/publish {uri, version, message} sends one diagnostic; fake/flood {n} sends n bulky ones.
Exits on stdin EOF or `exit`; ignores didChange with a version not above the stored one.
"""

import json
import os
import sys


def read():
    n = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length:"):
            n = int(line.split(b":")[1])
    return json.loads(sys.stdin.buffer.read(n))


def send(m):
    b = json.dumps(m).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(b)}\r\n\r\n".encode() + b)
    sys.stdout.buffer.flush()


def publish(uri, version, message):
    rng = {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}
    send(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "version": version, "diagnostics": [{"range": rng, "severity": 2, "message": message}]},
        }
    )


docs: dict[str, list] = {}  # uri -> [version, text]


def handle(m):
    method, rid, p = m.get("method"), m.get("id"), m.get("params")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"capabilities": {}}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": rid, "result": None})
    elif method == "exit":
        sys.exit(0)
    elif method == "textDocument/didOpen":
        d = p["textDocument"]
        docs[d["uri"]] = [d["version"], d["text"]]
        publish(d["uri"], d["version"], f"d{d['version']}")
    elif method == "textDocument/didChange":
        d = p["textDocument"]
        if d["uri"] in docs and d["version"] > docs[d["uri"]][0]:
            docs[d["uri"]] = [d["version"], p["contentChanges"][-1]["text"]]
            publish(d["uri"], d["version"], f"d{d['version']}")
    elif method == "textDocument/didClose":
        docs.pop(p["textDocument"]["uri"], None)
    elif method == "textDocument/hover" and os.environ.get("FAKE_SILENT"):
        pass  # never answers: drives the client timeout
    elif method == "textDocument/hover":
        v, text = docs.get(p["textDocument"]["uri"], [0, ""])
        send({"jsonrpc": "2.0", "id": rid, "result": {"contents": f"v{v}:{text.split(chr(10))[0]}"}})
    elif method == "fake/publish":
        publish(p["uri"], p["version"], p["message"])
    elif method == "fake/flood":
        for i in range(p["n"]):
            publish(f"file:///flood/{i}", 1, "x" * 1000)
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "result": None})


while True:
    handle(read())
