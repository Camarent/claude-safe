"""agent_files: a local-model (Strata) tool loop that checks and ranks the files a request needs, across every indexed repo.

The model gets the request, optional candidate leads (what an earlier scout found) and read-only tools (code index, language servers, git, and a confined shell when BOXES is set):
  find(repo, query)            lp find: ranked file ranges with a keyword or vector match
  outline(repo, file)          symbols of one file
  read(repo, file, start, end) a slice of a file (at most 80 lines)
  refs(repo, name)             who uses a name
  git(repo, args)              read-only git: ls-files, log, grep, show --stat
It answers with ONE json object: {"code": [...], "tests": [...], "docs": [...], "data": [...]} (at most 15 each; {"files": [...]} also parses), entries {"repo", "path", "why"}.
The loop is capped (MAX_TURNS); when the cap is hit the model is told to answer now. Any failure returns None so the caller can fall back."""

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pipeline import gitx  # noqa: E402
from pipeline.configuration import EXPLORE_MAX_TURNS  # noqa: E402
from pipeline.shellwords import short_flag  # noqa: E402
from pipeline.util import swallowed  # noqa: E402 - imports below the sys.path insert at line 22

MAX_TURNS = EXPLORE_MAX_TURNS
BUDGET_S = 240  # wall clock for the whole loop: past it the model is told to answer now. Below the 300 s limit of the plan context step (the answer call and the file digest still follow); 3 of 4 real runs hit that limit at 600
MAX_TOOL_ERRORS = 4  # consecutive tool results starting "error:": the explorer is lost, it is told to answer now
MAX_PER_CATEGORY = 20  # files per category (code, tests, docs, data) in the final answer
KEEP_RESULTS = 0  # tool-result turns kept in full; older ones keep only their first HEAD_CHARS so each call stays small (0 = keep all)
HEAD_CHARS = 300
LEDGER_MAX = 150  # files kept in the running list the model sees on every call
PATH_RE = re.compile(r"[\w.@/-]+\.\w{1,6}")
STATS: dict[
    str, Any
] = {}  # last agent_files() run: calls, model_secs, slowest_call, tool_calls, tool_errors (results starting "error:"), first_tool_error, tool_secs, slowest_tool, turns, ended (read by plan_run for the context metric)
CALL_TIMEOUT_S = 300  # one model call; a hung server returns None so the caller falls back
MAX_RESULT = 3000  # chars of one tool result handed back to the model
READ_LINES = 80
THINKING = True  # False sends thinking disabled: faster turns, no hidden tokens

TOOLS = [
    {
        "name": "find",
        "description": "Search the code index of one repo. Returns ranked file line ranges.",
        "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "query": {"type": "string"}}, "required": ["repo", "query"]},
    },
    {
        "name": "outline",
        "description": "List the symbols of one file.",
        "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "file": {"type": "string"}}, "required": ["repo", "file"]},
    },
    {
        "name": "read",
        "description": f"Read lines start..end of a file (at most {READ_LINES} lines).",
        "input_schema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "file": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["repo", "file", "start", "end"],
        },
    },
    {
        "name": "refs",
        "description": "Find the places that use a symbol name.",
        "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "name": {"type": "string"}}, "required": ["repo", "name"]},
    },
    {
        "name": "git",
        "description": (
            "Read-only git in one repo. `args` is the git command line without the word git. Allowed: ls-files [pathspec], "
            "log [-n N] [--name-only|--stat] [-S text] [-- path], grep -n -i <pattern> [-- pathspec], show --stat <rev>. "
            "Use ls-files to list files by folder or glob, grep to find files containing a text, log --name-only to see which files changed together."
        ),
        "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "args": {"type": "string"}}, "required": ["repo", "args"]},
    },
]

GIT_OK = ("ls-files", "log", "grep", "show")
BOXES: dict[str, str] = {}  # {repo name: live repo dir}; when set, the read-only `bash` tool runs there, confined by safeshell (see explore.py)
LSP = {"def": "Where a symbol is defined (language server). `file` and `symbol`.", "hover": "Type and doc of a symbol (language server). `file` and `symbol`."}
TOOLS += [
    {
        "name": n,
        "description": d,
        "input_schema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "file": {"type": "string"}, "symbol": {"type": "string"}},
            "required": ["repo", "file", "symbol"],
        },
    }
    for n, d in LSP.items()
]
TOOLS += [
    {
        "name": "symbols",
        "description": "Where a name is defined, project-wide (exact, prefix, substring).",
        "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "name": {"type": "string"}}, "required": ["repo", "name"]},
    },
    {
        "name": "diag",
        "description": "Type or compile errors in one file (language server).",
        "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "file": {"type": "string"}}, "required": ["repo", "file"]},
    },
    {
        "name": "bash",
        "description": "Read-only shell in one repo, relative paths only. Allowed: head tail grep egrep sed(-n) ls cat wc sort uniq find echo cut, git ls-files|log|grep|show|blame, lp find|nav. Pipes and && allowed; no writes, no network. Output is cut at 6000 chars.",
        "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "cmd": {"type": "string"}}, "required": ["repo", "cmd"]},
    },
]
_ORDER = ("find", "outline", "def", "refs", "hover", "symbols", "diag", "read", "git", "bash")
TOOLS.sort(key=lambda t: _ORDER.index(t["name"]))

PROMPT = (
    "TOOLS YOU HAVE: `find` (code index: keyword + vector search over every repo), `outline`, `def`, `refs`, `hover`, `symbols` (language servers: exact, resolved, cross-file), `diag`, `read`, `git`, "
    "and sometimes `bash` (read-only shell in one repo; relative paths). "
    "`bash` takes ONE command per call, on one line: no backticks, `$(...)`, `${...}`, heredoc (`<<`), newlines, absolute paths, `..`, or redirects (except `2>&1` and `> /dev/null`). "
    "Pipes between allowed read-only commands are fine; `git` allows only ls-files, log, grep, show, blame; `lp` allows only find and nav; no `sed -i`, no `find -exec`. Prefer the index and language-server tools to locate code; use git or bash for listing and text search.\n\n"
    "You help a planner who will NOT explore the code. From the REQUEST, find the files he must read or that will change: code, tests, contracts, schemas, UI strings. "
    "Use the tools to check where each problem of the request really lives; do not guess from names. "
    "Repos you may search are listed below; ignore a repo the request tells you to leave alone or has nothing to do with it. "
    "A request about a visible symptom (a screen, a count, a text) usually also touches the layers below it: the data contract (CONTRACT.md, schema files), the validator or checker that enforces it, the shared logic the UI calls, translations and the tests of each. "
    "Read the ARCHITECTURE MAP below first to learn which folder owns which layer, then look in each layer; do not stop at the first file that shows the symptom. "
    "You have no file list: use `git ls-files` (with a folder or glob) to see what a folder holds, `git grep` for a literal text, and `git log --name-only -n 5 -- <path>` to see which files change together with a file. "
    "Prefer `find` with a question over reading whole files; use `read` only on a short range. Stop searching once every problem of the request has its code, contract, test and UI string located. "
    "`path` is relative to the repo: never put the repo name in front of it. "
    "Leads from an earlier scout may be wrong or incomplete: verify them, drop the wrong ones, add what is missing. "
    "When you know enough, answer with ONE json object and nothing else: "
    '{"code": [...], "tests": [...], "docs": [...], "data": [...]}, each list most important first, at most 20 entries each, every entry {"repo": "<repo name>", "path": "<path relative to the repo>", "why": "<six words>"}. '
    "code = source files that change or must be read; tests = test files and fixtures of those; docs = CONTRACT.md, README.md, docs/ and module docs that describe the changed behaviour; "
    "data = schemas, vectors, translations (i18n), content and config. Fill every category the request touches: look for the tests and docs of each changed file on purpose, not only code.\n\n"
)


def _lp(repo, *args, t=60):
    try:
        return subprocess.run(["lp", *args], cwd=repo, capture_output=True, text=True, timeout=t, check=False).stdout
    except (OSError, subprocess.TimeoutExpired) as ex:
        swallowed("agent_files._lp", ex)  # lp missing or too slow: the tool result is empty
        return ""


def _repo_rel(root, path):
    """Repo-relative path of PATH (absolute or relative; symlinks and `..` resolved); None when it is not a path inside ROOT."""
    base = os.path.realpath(root)
    rel = os.path.relpath(os.path.realpath(os.path.join(base, path)), base)
    return None if rel in (os.curdir, os.pardir) or rel.startswith(os.pardir + os.sep) else rel


class _Final(Exception):
    """A tool result that is returned as is: an error line, or output already cut to its own limit."""


_NAV_ARGS = {  # tool -> (nav subcommand, argument keys, timeout)
    "outline": ("outline", ("file",), 60),
    "refs": ("refs", ("name",), 60),
    "def": ("def", ("file", "symbol"), 90),
    "hover": ("hover", ("file", "symbol"), 90),
    "symbols": ("symbols", ("name",), 90),
    "diag": ("diag", ("file",), 90),
}


def _tool_bash(a):
    if a.get("repo") not in BOXES:
        raise _Final("error: no shell in this run")
    import safeshell

    raise _Final(safeshell.run(str(a["cmd"]), BOXES[a["repo"]])[: MAX_RESULT * 2])


def _tool_git(root, a):
    import shlex

    argv = shlex.split(str(a["args"]))
    if not argv or argv[0] not in GIT_OK or any(x.startswith(("--output", "--ext-diff", "-c")) for x in argv):
        raise _Final("error: allowed git commands: " + ", ".join(GIT_OK))
    if argv[0] == "grep" and any(x == "-f" or x.startswith(("--file", "--open-files")) or short_flag(x, "fO") for x in argv):
        raise _Final("error: git grep: no -f, --file or -O (reads a file or runs a pager)")
    return gitx.run(root, "--no-pager", *argv).stdout


def _tool_read(root, a):
    rel = _repo_rel(root, str(a["file"]))
    if rel is None:
        raise _Final("error: path outside the repo")
    p = os.path.join(os.path.realpath(root), rel)
    s, e = max(int(a["start"]), 1), int(a["end"])
    with open(p, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()[s - 1 : min(e, s + READ_LINES - 1)]
    return "\n".join(f"{s + i}: {x}" for i, x in enumerate(lines))


def _tool_out(root, name, a):
    """Raw result of tool NAME; raises _Final for a result that skips the common cut."""
    if name == "find":
        return _lp(root, "find", str(a["query"]), "--no-model")
    if name in _NAV_ARGS:
        sub, keys, t = _NAV_ARGS[name]
        return _lp(root, "nav", sub, *[str(a[k]) for k in keys], t=t)
    if name == "bash":
        return _tool_bash(a)
    if name == "git":
        return _tool_git(root, a)
    if name == "read":
        return _tool_read(root, a)
    raise _Final("error: unknown tool")


def run_tool(repos, name, a):
    """Result text of one tool call; an unknown repo or a bad argument gives a one-line error, never an exception."""
    root = repos.get(a.get("repo"))
    if root is None:
        return "error: unknown repo; use one of " + ", ".join(repos)
    try:
        out = _tool_out(root, name, a)
    except _Final as fin:
        return str(fin)
    except (KeyError, ValueError, OSError, subprocess.TimeoutExpired) as ex:
        return f"error: {str(ex)[:100]}"
    return (out.strip() or "(no result)")[:MAX_RESULT]


def _post(messages, tools=True):
    from search.strata import messages as strata_messages
    from search.strata import strata_model

    body = {"model": strata_model(), "max_tokens": 16000, "messages": messages}
    if not THINKING:
        body["thinking"] = {"type": "disabled"}
    if tools:
        body["tools"] = [t for t in TOOLS if t["name"] != "bash" or BOXES]
    return strata_messages(body, CALL_TIMEOUT_S)


def parse_files(text, repos):
    """[(repo, path, why, category)] from the model's final json; entries of an unknown repo are dropped. None when there is no json object."""
    try:
        obj = json.JSONDecoder().raw_decode(text[text.index("{") :])[0]
        items = obj.get("files") or [
            {**i, "category": k} for k in ("code", "tests", "docs", "data") for i in (obj.get(k) or [])[:MAX_PER_CATEGORY] if isinstance(i, dict)
        ]
    except (ValueError, AttributeError):  # cut off by max_tokens: keep the complete entries
        items = []
        for m in re.finditer(r"\{[^{}]*\}", text):
            with contextlib.suppress(ValueError):
                items.append(json.loads(m.group(0)))
        if not items:
            return None
    out = []
    for i in items:
        if not (isinstance(i, dict) and i.get("repo") in repos and isinstance(i.get("path"), str)):
            continue
        rel = _repo_rel(repos[i["repo"]], i["path"])
        if rel is not None and os.path.isfile(os.path.join(repos[i["repo"]], rel)):
            out.append((i["repo"], rel, str(i.get("why", ""))[:80], str(i.get("category", ""))))
    return out


def arch_map(repos, limit=12000, readme=1500):
    """The docs/ARCHITECTURE.md head (and the README.md head) of each repo that has one; the planner's map of which folder owns which layer."""
    out = []
    for name, root in repos.items():
        line = f"- {name}: {root}"
        try:
            with open(os.path.join(root, "course.json"), encoding="utf-8") as fh:
                c = json.load(fh)
            line += f" (a course: id {c.get('id')}, title {c.get('title')}; content in content/*.jsonl)"
        except FileNotFoundError:
            pass  # not a course: no course.json
        except (OSError, ValueError, AttributeError) as ex:
            swallowed("agent_files.arch_map course.json", ex)
        out.append(line)
    out.append("")
    for name, root in repos.items():
        try:
            with open(os.path.join(root, "docs", "ARCHITECTURE.md"), encoding="utf-8") as fh:
                out.append(f"## {name}\n" + fh.read()[:limit])
        except FileNotFoundError:
            pass  # optional doc: most repos have none
        except OSError as ex:
            swallowed("agent_files.arch_map ARCHITECTURE.md", ex)
        try:
            with open(os.path.join(root, "README.md"), encoding="utf-8") as fh:
                out.append(f"## {name} README.md\n" + fh.read()[:readme])
        except FileNotFoundError:
            pass  # optional doc: a repo may have no README
        except OSError as ex:
            swallowed("agent_files.arch_map README.md", ex)
    return "\n\n".join(out)


def describe(path):
    """One short line on what a file is: its first doc/comment line, else its first non-empty line."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = ""
            for _, line in zip(range(25), fh, strict=False):
                t = line.strip()
                if not t or t.startswith(("#!", "<?xml")):
                    continue
                first = first or t
                body = t.lstrip("/!#*\"' ").strip()
                if t.startswith(("//!", "///", "#", "/*", "*", '"""', "'''", "<!--")) and len(body) > 3:
                    return body[:90]
            return first[:90]
    except OSError as ex:
        swallowed("agent_files.describe", ex)  # unreadable file: no description line
        return ""


def note_files(ledger, repos, text):
    """Add every existing file a tool result mentions to ledger {(repo, path): description}; capped at LEDGER_MAX."""
    for m in PATH_RE.findall(text):
        for name, root in repos.items():
            rel = _repo_rel(root, m)
            if rel and (name, rel) not in ledger and len(ledger) < LEDGER_MAX and os.path.isfile(os.path.join(root, rel)):
                ledger[(name, rel)] = describe(os.path.join(root, rel))


def ledger_files(ledger):
    """The ledger as the final-answer list [(repo, path, why, category)]: a fallback when the model gives no json."""
    return [(n, p, d[:80], "") for (n, p), d in ledger.items()]


def _end_state(got, ledger, turn, lost, last):
    """-> (files, why the loop ended): no json but a ledger gives the ledger files and `ledger`."""
    if got is None and ledger:  # the model never produced json: its tool results still found files
        return ledger_files(ledger), "ledger"
    ended = "no-json" if got is None else "cap" if turn == MAX_TURNS else "tool-errors" if lost else "budget" if last else "answer"
    return got, ended


def with_ledger(msgs, ledger):
    """msgs as sent to the model: the last user turn gets the list of files found so far (msgs itself is not changed)."""
    if not ledger or not isinstance(msgs[-1]["content"], list):
        return msgs
    lines = "\n".join(f"- {n}:{p}{' - ' + d if d else ''}" for (n, p), d in ledger.items())
    note = {
        "type": "text",
        "text": "FILES FOUND SO FAR (every file your tool results mentioned, with what it is; old results are cut, this list is not):\n" + lines,
    }
    return [*msgs[:-1], {**msgs[-1], "content": [*msgs[-1]["content"], note]}]


def _call_descriptions(msgs):
    """{tool_use id: 'name input'} of every tool call in msgs."""
    desc = {}
    for m in msgs:
        for b in m["content"] if isinstance(m["content"], list) else []:
            if b.get("type") == "tool_use":
                desc[b["id"]] = f"{b.get('name')} {json.dumps(b.get('input', {}))[:90]}"
    return desc


def _is_result_turn(m):
    return m["role"] == "user" and isinstance(m["content"], list) and any(b.get("type") == "tool_result" for b in m["content"])


def trim_results(msgs, keep=None):
    """Cut the tool results of all but the last `keep` result turns to their first HEAD_CHARS, headed by the call that made them. Edits msgs in place."""
    keep = KEEP_RESULTS if keep is None else keep
    if not keep:
        return
    desc = _call_descriptions(msgs)
    for m in [m for m in msgs if _is_result_turn(m)][:-keep]:
        for b in m["content"]:
            text = str(b["content"]) if b.get("type") == "tool_result" else ""
            if len(text) > HEAD_CHARS and not text.startswith("(cut"):
                b["content"] = f"(cut from {len(text)} chars, call: {desc.get(b['tool_use_id'], '?')})\n{text[:HEAD_CHARS]}"


def _head(repos, request, leads, arch, listing, tail):
    """The first user message: prompt, repos, request and the optional context blocks."""
    head = PROMPT + "REPOS: " + ", ".join(repos) + "\n\nREQUEST:\n" + request[:8000]
    if listing:
        head += (
            "\n\nFILE LISTS (every file of each repo). Choose the files from these lists, judging by path against the request, layer by layer; "
            "then use the tools only to check a doubtful file or to find what else belongs next to it:\n"
            + "\n".join(f"## {n}\n" + "\n".join(fs) for n, fs in listing.items())
        )
    amap = arch_map(repos) if arch is None else arch
    if amap:
        head += "\n\nARCHITECTURE MAP:\n" + amap
    if leads:
        head += "\n\nLEADS FROM AN EARLIER SCOUT (unverified):\n" + leads[:14000]
    if tail:
        head += "\n\nREMINDER (the key facts again):\n" + tail
    return head


def _timed(kind, fn, *a, **kw):
    """Call FN and add its time and count to STATS under KIND (model or tool)."""
    t = time.time()
    try:
        return fn(*a, **kw)
    finally:
        d = time.time() - t
        STATS[kind + "_secs"] += d
        slow = "slowest_" + ("call" if kind == "model" else "tool")
        STATS[slow] = max(STATS[slow], d)
        STATS["calls" if kind == "model" else "tool_calls"] += 1


def _text_of(blocks):
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _final_answer(post, msgs, out, repos, ledger, turn, lost, last, log):
    """The loop's last step: parse the json (one stern retry when there is none) and set STATS["ended"]. -> files or None."""
    blocks = out.get("content", [])
    text = _text_of(blocks)
    got = parse_files(text, repos)
    if got is None:  # prose or tool calls as plain text instead of the json (also on an early turn): one stern retry
        msgs.append({"role": "assistant", "content": text or "(no answer)"})
        msgs.append(
            {
                "role": "user",
                "content": "The tools are gone. Output ONLY the json object now, built from what you already found and from the leads.",
            }
        )
        out = _timed("model", post, msgs, tools=False)
        blocks = out.get("content", [])
        text = _text_of(blocks)
        got = parse_files(text, repos)
    got, STATS["ended"] = _end_state(got, ledger, turn, lost, last)
    if got is None and log is not None:
        log.append("no json; model said: " + text[:300].replace("\n", " ") + f" | stop={out.get('stop_reason')} blocks={[b.get('type') for b in blocks]}")
    return got


def _run_calls(calls, run, repos, ledger, log, turn):
    """Run the tool calls of one turn. -> (tool_result blocks, number of error results)."""
    results, bad = [], 0
    for c in calls:
        r = _timed("tool", run, repos, c["name"], c.get("input", {}))
        bad += r.startswith("error:")
        if r.startswith("error:"):
            STATS["tool_errors"] += 1
            STATS.setdefault("first_tool_error", r[:100])
        if log is not None:
            log.append(f"turn {turn + 1}: {c['name']} {json.dumps(c.get('input', {}))[:300]} -> {len(r)} chars: {r[:200]!r}")
        note_files(ledger, repos, r)
        results.append({"type": "tool_result", "tool_use_id": c["id"], "content": r})
    return results, bad


def _is_last(turn, t0, lost):
    """True when this turn must end the loop: turn cap, time budget or too many tool errors."""
    return turn == MAX_TURNS or (turn > 0 and time.time() - t0 > BUDGET_S) or lost


def _log_said(log, turn, blocks):
    said = _text_of(blocks).strip()
    if log is not None and said:
        log.append(f"turn {turn + 1}: model said: {said[:400]!r}")


def agent_files(repos, request, leads="", post=_post, run=run_tool, log=None, arch=None, listing=None, tail=""):
    """repos: {name: dir}. -> [(repo, path, why, category)] most important first (category code|tests|docs|data, "" for a flat {"files"} answer), or None when the loop failed. LOG, a list, receives one line per turn."""
    msgs = [{"role": "user", "content": _head(repos, request, leads, arch, listing, tail)}]
    ledger: dict = {}
    STATS.clear()
    STATS.update(calls=0, model_secs=0.0, slowest_call=0.0, tool_calls=0, tool_errors=0, tool_secs=0.0, slowest_tool=0.0, turns=0, ended="")
    t0 = time.time()
    err_run = 0  # tool errors in a row
    try:
        for turn in range(MAX_TURNS + 1):
            lost = err_run >= MAX_TOOL_ERRORS
            last = _is_last(turn, t0, lost)
            if last:
                msgs[-1]["content"] = [
                    *msgs[-1]["content"],
                    {"type": "text", "text": "Answer now with the json object."},
                ]  # one user turn: tool results plus this
            STATS["turns"] = turn + 1
            trim_results(msgs)
            out = _timed("model", post, with_ledger(msgs, ledger), tools=not last)
            blocks = out.get("content", [])
            calls = [b for b in blocks if b.get("type") == "tool_use"]
            if not calls or last:
                return _final_answer(post, msgs, out, repos, ledger, turn, lost, last, log)
            _log_said(log, turn, blocks)
            msgs.append({"role": "assistant", "content": [b for b in blocks if b.get("type") in ("text", "tool_use")]})
            results, bad = _run_calls(calls, run, repos, ledger, log, turn)
            msgs.append({"role": "user", "content": results})
            err_run = err_run + bad if bad else 0  # a good result resets the run only when every result of the turn is good
    except Exception as ex:  # Strata or network trouble never fails the planner
        swallowed("agent_files.explore", ex)
        STATS["ended"] = "error:" + type(ex).__name__
        if log is not None:
            log.append(f"failed: {type(ex).__name__} {str(ex)[:200]}")
        return None
