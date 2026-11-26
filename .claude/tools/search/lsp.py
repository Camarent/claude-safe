#!/usr/bin/env python3
"""IDE-style code navigation for agents: one-shot client for the language servers in the image.

  lsp.py def|refs|hover FILE LINE[:COL] | FILE SYMBOL     where is it defined / who uses it / type+doc
  lsp.py outline FILE                                     symbols in one file
  lsp.py symbols NAME [--lang py|js|ts|cpp|rs]        where is NAME defined, project-wide (ctags: exact, prefix, substring)
  lsp.py diag FILE                                        type/compile errors in one file

Positions are 1-based. Give a SYMBOL name instead of LINE:COL and the first whole-word match in FILE is used.
Servers: pyright (py), tsc --lsp (TypeScript 7 native, js/ts), clangd (c/cpp, needs compile_commands.json), rust-analyzer (rs).
Output is `path:line:col  text`, paths relative to the project root (nearest .git / config file above FILE, else cwd).
Exit 2 = no server for that file type, 3 = server failed. diag: never a false clean: NOT CHECKED (exit 3) if nothing was checked; falls back to py_compile / node --check. `lp nav` runs these ops on the warm pool (lsp_pool.py); this file is the per-call fallback and the path for `symbols`."""

import json
import os
import re
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
# lsp.py also runs as a script (python -I search/lsp.py): the tools root is not on sys.path before the insert above
from pipeline.configuration import tool_setting  # noqa: E402  # the tools root is on sys.path (inserted above)

from search.lsp_client import (  # noqa: E402
    EXT,
    MARKERS,
    SERVERS,
    Client,
    path,
    poll,
    root_of,
    sources,
    uri,
)

# re-exported: tests and callers use lsp.<name>; tests patch lsp.time (the module lsp_client.poll uses)
__all__ = ["EXT", "MARKERS", "SERVERS", "Client", "path", "poll", "root_of", "sources", "time", "uri"]

KIND = {
    5: "class",
    6: "method",
    9: "ctor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "const",
    22: "member",
    23: "struct",
    2: "module",
    8: "field",
    7: "property",
}


def locs(res):
    out = []
    for r in res if isinstance(res, list) else [res] if res else []:
        u, rg = (r["targetUri"], r.get("targetSelectionRange") or r["targetRange"]) if "targetUri" in r else (r["uri"], r["range"])
        out.append((path(u), rg["start"]["line"] + 1, rg["start"]["character"] + 1))
    return sorted(set(out))


def show(items, root):
    for f, lineno, c in items:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read().split("\n")[lineno - 1].strip()[:110]
        except OSError:
            text = ""
        print(f"{os.path.relpath(f, root)}:{lineno}:{c}  {text}")
    if not items:
        print("(none)")


def position(f, spec):
    if re.fullmatch(r"\d+(:\d+)?", spec):
        lineno, _, c = spec.partition(":")
        return int(lineno), int(c or 1)
    with open(f, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    for i, line in enumerate(text.split("\n"), 1):
        m = re.search(r"(?<![\w$])" + re.escape(spec) + r"(?![\w$])", line)
        if m:
            return i, m.start() + 1
    sys.exit(f"symbol '{spec}' not found in {f}")


CTAGS_LANG = {"py": "Python", "js": "JavaScript", "ts": "TypeScript", "cpp": "C,C++", "rs": "Rust"}


def symbols(q, root, lang):
    """Project-wide name lookup via universal-ctags: exact matches first, then prefix, then substring."""
    langs = CTAGS_LANG[lang] if lang else ",".join(CTAGS_LANG.values())
    r = subprocess.run(
        [
            "ctags",
            "-R",
            "--output-format=json",
            "--fields=+nKS",
            f"--languages={langs}",
            "-f",
            "-",
            *[f"--exclude={x}" for x in ("node_modules", ".git", "target", "dist", "build", "__pycache__", ".venv", "*.min.js")],
            root,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    ql, hits = q.lower(), []
    for line in r.stdout.splitlines():
        try:
            t = json.loads(line)
        except ValueError:  # KEEP: rg prints one JSON object per line; a non-JSON line is skipped, not an error
            continue
        n = t.get("name", "")
        rank = 0 if n == q else 1 if n.lower().startswith(ql) else 2 if ql in n.lower() else None
        if rank is not None:
            hits.append((rank, t["path"], t["line"], t.get("kind", "sym"), (t.get("scope") + "::" if t.get("scope") else "") + n))
    for _rank, f, lineno, k, n in sorted(hits)[:40]:
        print(f"{os.path.relpath(f, root)}:{lineno}  {k} {n}")
    if not hits:
        print("(none)")


def word_at(f, line, col):
    with open(f, encoding="utf-8", errors="replace") as fh:
        text = fh.read().split("\n")[line - 1]
    return next((m.group(0) for m in re.finditer(r"[\w$]+", text) if m.start() < col <= m.end()), "")


def textrefs(word, root, lang, seen):
    """Word-match hits the server missed (CommonJS destructuring, globals). Marked as text, not verified."""
    if not word:  # no symbol under the cursor: rg -nw "" would match every line
        return
    globs = ["--glob=*.js", "--glob=*.mjs", "--glob=*.cjs", "--glob=*.jsx", "--glob=*.ts", "--glob=*.tsx"]
    r = subprocess.run(
        ["rg", "-nw", "--no-heading", "--column", *globs, "--glob=!node_modules", "--glob=!target", "--glob=!dist", word, root],
        capture_output=True,
        text=True,
        check=False,
    )
    extra = []
    for line in r.stdout.splitlines()[:200]:
        f, lineno, c, _ = line.split(":", 3)
        if (os.path.abspath(f), int(lineno), int(c)) not in seen:
            extra.append((os.path.abspath(f), int(lineno), int(c)))
    if extra:
        print(f"# {len(extra)} more word-match hits (text search, not verified as the same symbol):")
        show(extra[:40], root)


def not_checked(f, lang, why):
    """Last resort when no language server answered: syntax-only check, or an explicit NOT CHECKED (exit 3), never a false clean."""
    cmd = [sys.executable, "-m", "py_compile", f] if lang == "py" else ["node", "--check", f] if lang == "js" else None
    if cmd:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=tool_setting("LSP_RUN_TIMEOUT_S"), check=False)
            msg = r.stderr.strip().splitlines() or [""]
            if r.returncode:
                print(f"{f}  syntax error ({'node --check' if lang == 'js' else 'py_compile'}; {why}; type errors NOT CHECKED): {msg[-1][:150]}")
                return 1
            print(f"syntax OK only ({why}); type/semantic errors NOT CHECKED")
            return 3
        except (OSError, subprocess.TimeoutExpired):  # KEEP: checker missing or hung; the NOT CHECKED line below reports it
            pass
    print(f"NOT CHECKED: {why}")
    return 3


def _cmd_symbols(a):
    i = a.index("--lang") if "--lang" in a else -1
    lang = a[i + 1] if 0 <= i < len(a) - 1 else None
    q = a[1]
    symbols(q, os.getcwd(), lang if lang in CTAGS_LANG else None)
    return 0


def _no_server(op, f):
    if op == "diag":
        print(f"NOT CHECKED: no language server for {f}")
        return 3
    print(f"no language server for {f}")
    return 2


RUST_LOAD_WAIT = 22  # seconds added to the poll budget; poll() returns as soon as the answer arrives


def _open_for(c, op, f, lang, root):
    """Open F (and, for refs, the project's other sources); return extra seconds to poll."""
    c.open(f, ts_check=(op == "diag" and lang == "js"))  # JS: in-memory // @ts-check, so no tsconfig is needed
    if op == "refs" and lang in ("js", "ts", "cpp"):  # tsc --lsp and clangd (no background index) only search files they have opened
        c.ensure_open_all(root, lang, skip=(os.path.abspath(f),))  # stat shortcut: unchanged files are not resent
        return 4 if lang == "cpp" else 0  # clangd indexes opened files in the background: poll longer instead of sleeping
    return RUST_LOAD_WAIT if lang == "rust" else 0  # rust-analyzer needs several seconds to load a crate ("content modified" until then)


def _ask_req(c, method, params, parse, enough, budget):
    """Request METHOD and parse the answer; servers answer early while the project loads, so retry until enough or the budget passes."""

    def go():  # one request per poll step: rust-analyzer answers "content modified" while it loads, poll() retries inside the budget
        try:
            return parse(c.req(method, params))
        except RuntimeError as e:
            if "content modified" not in str(e).lower():
                raise
            return []

    return poll(go, enough, budget)


def _ask_locs(c, op, f, pos, method, wait_extra, extra=None):
    """Request locations; servers answer early while the project loads, so retry until enough or the timeout."""
    params = {"textDocument": {"uri": uri(f)}, "position": pos, **(extra or {})}
    return _ask_req(c, method, params, locs, lambda r: len(r) >= (2 if op == "refs" else 1), 2.5 + wait_extra)


def _target(f, spec):
    line, col = position(f, spec)
    return line, col, {"line": line - 1, "character": col - 1}


def _cmd_def(c, a, f, lang, root):
    wait_extra = _open_for(c, "def", f, lang, root)
    _line, _col, pos = _target(f, a[2])
    show(_ask_locs(c, "def", f, pos, "textDocument/definition", wait_extra), root)
    return 0


def _cmd_refs(c, a, f, lang, root):
    wait_extra = _open_for(c, "refs", f, lang, root)
    line, col, pos = _target(f, a[2])
    r = _ask_locs(c, "refs", f, pos, "textDocument/references", wait_extra, {"context": {"includeDeclaration": True}})
    show(r, root)
    if lang in ("js", "ts"):
        textrefs(word_at(f, line, col), root, lang, set(r))
    return 0


def _hover_text(v):
    if isinstance(v, dict):
        return v.get("value", "")
    if isinstance(v, list):
        return "\n".join(x.get("value", x) if isinstance(x, dict) else x for x in v)
    return v


def _cmd_hover(c, a, f, lang, root):
    _open_for(c, "hover", f, lang, root)
    _line, _col, pos = _target(f, a[2])
    h = c.req("textDocument/hover", {"textDocument": {"uri": uri(f)}, "position": pos})
    print(_hover_text((h or {}).get("contents", "")).strip() or "(none)")
    return 0


def _cmd_outline(c, a, f, lang, root):
    # A file with no symbols (empty file) waits the whole budget (2.5 s + wait_extra) before rc 0: an empty answer cannot be told from "still loading".
    wait_extra = _open_for(c, "outline", f, lang, root)
    syms = _ask_req(c, "textDocument/documentSymbol", {"textDocument": {"uri": uri(f)}}, lambda r: r or [], lambda r: len(r) > 0, 2.5 + wait_extra)
    for s in syms:
        rg = s.get("selectionRange") or s["location"]["range"]
        print(f"{rg['start']['line'] + 1}:{rg['start']['character'] + 1}  {KIND.get(s['kind'], 'sym')} {s['name']}")
    return 0


def _print_diag(d, f, lang, root):
    if lang == "js":
        d["range"]["start"]["line"] -= 1  # undo the in-memory prefix line
    print(
        f"{os.path.relpath(f, root)}:{d['range']['start']['line'] + 1}:{d['range']['start']['character'] + 1}  {['', 'error', 'warning', 'info', 'hint'][d.get('severity', 3)]}: {d['message'].splitlines()[0][:120]}"
    )


def _cmd_diag(c, a, f, lang, root):
    _open_for(c, "diag", f, lang, root)
    try:  # pull model first (pyright publishes nothing for a clean file)
        pulled = c.req("textDocument/diagnostic", {"textDocument": {"uri": uri(f)}}, 20) if lang in ("py", "js", "ts") else None
        if pulled and "items" in pulled:
            c.diags[uri(f)] = pulled["items"]
    except (RuntimeError, TimeoutError):  # KEEP: pull diagnostics is optional; the push settle below covers servers without it
        pass
    if (lang not in ("py", "js", "ts") or uri(f) not in c.diags) and not c.settle(15 if lang != "rs" else 60, uri(f)):
        return not_checked(f, lang, "language server sent no diagnostics")
    ds = c.diags.get(uri(f), [])
    if lang == "js":
        ds = [d for d in ds if d["range"]["start"]["line"] >= 1]
    for d in sorted(ds, key=lambda d: d["range"]["start"]["line"]):
        _print_diag(d, f, lang, root)
    if not ds:
        print("(no diagnostics)")
    return 0


# sub-commands that talk to a language server: op -> handler(client, args, file, lang, root) -> exit code
COMMANDS = {"def": _cmd_def, "refs": _cmd_refs, "hover": _cmd_hover, "outline": _cmd_outline, "diag": _cmd_diag}


def run_op(op, a, f, lang, root, client):
    """Run one server op on CLIENT (any lifetime); return the exit code. Never reads the process cwd."""
    client.drain()
    try:
        return COMMANDS[op](client, a, f, lang, root)
    except (RuntimeError, TimeoutError, EOFError) as e:
        if op == "diag":
            return not_checked(f, lang, f"lsp error: {e}")
        print(f"lsp error: {e}")
        return 3


def main(a):
    if len(a) < 2 or a[0] not in (*COMMANDS, "symbols"):
        sys.exit(__doc__)
    op = a[0]
    if op == "symbols":
        return _cmd_symbols(a)
    f = a[1]
    lang = EXT.get(os.path.splitext(f)[1].lower())
    if not os.path.exists(f):
        sys.exit(f"{f}: no such file")
    if not lang:
        return _no_server(op, f)
    root = root_of(f, os.getcwd())
    try:
        c = Client(lang, root)
    except (OSError, EOFError, RuntimeError) as e:
        if op == "diag":
            return not_checked(f, lang, f"server failed to start: {e}")
        print(f"server failed to start: {e}")
        return 3
    try:
        return run_op(op, a, f, lang, root, c)
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
