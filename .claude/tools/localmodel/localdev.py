#!/usr/bin/env python3
"""Local developer: the Strata model does the token-heavy work, Claude reads a 3-line result.

  python3 -I /root/.claude/tools/localmodel/localdev.py pack --out .work/s/pack-x.md --goal "text or @file" [--max-words 700]
  python3 -I /root/.claude/tools/localmodel/localdev.py bulk --out .work/s/summary.md --instruction "text or @file" IN [IN ...]
  python3 -I /root/.claude/tools/localmodel/localdev.py code --task .work/s/task-1.md [--test "cmd"] [--paths a.py,b/c.py] [--id name] [--profile rust,js]
  python3 -I /root/.claude/tools/localmodel/localdev.py log [--last 10]

A task file can carry its own settings in the pipeline's usual header lines, so the command stays short:
  Predicted paths: a.py, b/c.py      Test (loop): `python3 -m unittest tests.test_x -q`
  Profiles: rust                   (optional; default: by file extension, see localdev/policy.json)
  Read also: ../other-repo/docs    (optional extra READ-ONLY directories, at most 3)
  Run also: cargo clippy -p core   (optional extra commands; must fit the ceiling in localdev/policy.json)
  turns: 40
Every run gets: localdev/profiles/base.md + profiles for the task + the project's short architecture (.localdev/architecture.md or the first
400 words of docs/ARCHITECTURE.md), appended to the system prompt. Every run is logged to .work/localdev/runs.jsonl.

pack  context pack for a planner/auditor: the model proposes search queries and judges the hits (search/ask_code.py), code copies the excerpts
      verbatim into ONE file, so every path:line cite is valid by construction
bulk  extraction/summary over files into ONE output file
code  a narrow task in an isolated git worktree (.work/localdev/<id>/wt): the model edits only the predicted paths and may run only the test
      command; afterwards THIS script reruns the test, reverts anything outside the predicted paths and writes report.md. In code mode the model may commit inside its own branch localdev/<id>; push, reset, rebase and clean are never allowed.
All three run `claude -p` against the local Strata server with an EMPTY Claude config (no login, no hooks, no MCP), and print 1-3 lines.
Env: STRATA_URL, STRATA_KEY/ANTHROPIC_AUTH_TOKEN, STRATA_MODEL, CONTEXT (e.g. 128k), LOCALDEV_CMD (tests: replaces the claude call).
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]
from pipeline import gitx  # noqa: E402
from pipeline.configuration import (  # noqa: E402
    FAIL,
    GIT_SAFE_ENV,
    LOCAL_AGENT_TIMEOUT_S,
    LOCAL_GOAL_CHARS,
    LOCAL_GOAL_SHORT_CHARS,
    LOCAL_MAX_TOKENS,
    LOCAL_MAX_TURNS,
    LONG_CMD_TIMEOUT_S,
    PASS,
    WORK_DIR,
    tool_cmd,
)
from pipeline.util import first_json_obj, run_capture, text_arg  # noqa: E402
from search.strata import strata_key, strata_model, strata_url  # noqa: E402

from localmodel.prompts import JSON_ONLY  # noqa: E402

try:
    from search.localq import slot
except ImportError:
    import contextlib

    def slot(kind="job", timeout=None):
        return contextlib.nullcontext()


def sh(args, cwd, **kw):
    kw["env"] = dict(kw.get("env") or os.environ, **GIT_SAFE_ENV)
    return run_capture(args, cwd, **kw)


def _git(cwd, *args):
    return gitx.run(cwd, *args)


LD_HOME = os.environ.get("LOCALDEV_HOME", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
TEST_PREFIX_KEYS = {
    "predicted paths": "paths",
    "test (loop)": "test",
    "test": "test",
    "profiles": "profiles",
    "read also": "read_also",
    "run also": "run_also",
}


def load_policy():
    return json.loads(Path(os.path.join(LD_HOME, "policy.json")).read_text(encoding="utf-8"))


def parse_header(text):
    """Settings from the task file's header lines (pipeline task format); backticks around commands are stripped."""
    out: dict = {}
    for line in text.splitlines()[:40]:
        m = re.match(r"\s*[-*]?\s*([A-Za-z() ]+?)\s*:\s*(.+)$", line)
        if m and m.group(1).strip().lower() in TEST_PREFIX_KEYS:
            k, v = TEST_PREFIX_KEYS[m.group(1).strip().lower()], m.group(2).strip()
            bt = re.search(r"`([^`]+)`", v)
            if k in ("test", "run_also") and bt:
                v = bt.group(1)
            out.setdefault(k, v)
        t = re.search(r"(?i)\bturns:\s*(\d+)", line)
        if t:
            out.setdefault("turns", int(t.group(1)))
    return out


def command_ok(cmd, policy):
    """A command is allowed when it starts with a ceiling prefix and holds no forbidden word; the part after qrun's `--` is checked too."""
    c = cmd.strip()
    q = f"{tool_cmd('qrun.py')} --"
    inner = c[len(q) :].strip() if c.startswith(q) else c
    if any(w in c for w in policy["forbidden_words"]):
        return False
    return any(inner.startswith(p) for p in policy["bash_prefixes"] if not p.startswith(q))


def _profile_names(kinds, policy, explicit, task_paths):
    """base + the kinds + the explicit profiles (comma list), else those the predicted paths' extensions map to."""
    names = ["base"]
    chosen = [x.strip() for x in (explicit or "").split(",") if x.strip()]
    if not chosen:
        ext = policy["profile_by_extension"]
        chosen = sorted({ext[os.path.splitext(p)[1]] for p in task_paths if os.path.splitext(p)[1] in ext})
    return names + [k for k in kinds if k not in names] + [c for c in chosen if c not in names]


def _architecture_text(root, policy):
    """-> (text, source) of the first architecture file that exists under ROOT, capped in words; ("", "") when none."""
    for src in policy["architecture_sources"]:
        fp = os.path.join(root, src)
        if os.path.isfile(fp):
            with open(fp, encoding="utf-8", errors="replace") as fh:
                words = fh.read().split()
            return "Project architecture (short):\n" + " ".join(words[: policy["architecture_max_words"]]), src
    return "", ""


def system_prompt(root, kinds, policy, explicit=None, task_paths=()):
    """base + profiles + short architecture -> one appended system prompt (capped)."""
    names = _profile_names(kinds, policy, explicit, task_paths)
    parts = []
    for n in names:
        fp = os.path.join(LD_HOME, "profiles", n + ".md")
        if not os.path.isfile(fp):
            sys.exit(f"localdev: unknown profile '{n}' (have: {sorted(f[:-3] for f in os.listdir(os.path.join(LD_HOME, 'profiles')))})")
        parts.append(Path(fp).read_text(encoding="utf-8").strip())
    arch, src = _architecture_text(root, policy)
    if arch:
        parts.append(arch)
        names.append("architecture:" + src)
    return "\n\n".join(parts), names


def log_run(root, rec):
    d = os.path.join(root, WORK_DIR, "localdev")
    os.makedirs(d, exist_ok=True)
    line = json.dumps(dict(rec, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))) + "\n"
    fd = os.open(os.path.join(d, "runs.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def ctx_tokens():
    c = os.environ.get("CONTEXT", "128k").lower()
    try:
        return int(c[:-1]) * 1024 if c.endswith("k") else int(c)
    except ValueError:
        return 65536


def run_agent(prompt, cwd, tools, allowed, max_turns=LOCAL_MAX_TURNS, timeout=LOCAL_AGENT_TIMEOUT_S, system=None, add_dirs=()):
    """-> dict(rc, turns, secs, reply). LOCALDEV_CMD (tests) runs instead of claude, with LOCALDEV_PROMPT in the env."""
    with slot("job"):  # at most LOCALQ_JOBS claude -p sessions on the local model at once
        return _run_agent(prompt, cwd, tools, allowed, max_turns, timeout, system, add_dirs)


def _run_agent(prompt, cwd, tools, allowed, max_turns, timeout, system, add_dirs):
    t0 = time.time()
    if os.environ.get("LOCALDEV_CMD"):
        p = sh(os.environ["LOCALDEV_CMD"], cwd, shell=True, env=dict(os.environ, LOCALDEV_PROMPT=prompt), timeout=timeout)  # nosec  # LOCALDEV_CMD is an operator-set test hook
        return {"rc": p.returncode, "turns": 0, "secs": round(time.time() - t0, 1), "reply": p.stdout[-300:]}
    m = strata_model()
    config_dir = tempfile.mkdtemp()
    env = dict(
        os.environ,
        CLAUDE_CONFIG_DIR=config_dir,
        ANTHROPIC_BASE_URL=strata_url(),
        ANTHROPIC_AUTH_TOKEN=strata_key(),
        ANTHROPIC_MODEL=m,
        ANTHROPIC_SMALL_FAST_MODEL=m,
        CLAUDE_CODE_MAX_CONTEXT_TOKENS=str(ctx_tokens()),
        CLAUDE_CODE_MAX_OUTPUT_TOKENS="4096",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        DISABLE_TELEMETRY="1",
    )
    env.pop("ANTHROPIC_API_KEY", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(GIT_SAFE_ENV)
    cmd = [
        "claude",
        "-p",
        prompt,
        "--tools",
        tools,
        "--allowedTools",
        *allowed,
        "--permission-mode",
        "dontAsk",
        "--max-turns",
        str(max_turns),
        "--output-format",
        "json",
    ]
    if system:
        cmd += ["--append-system-prompt", system]
    for d in add_dirs:
        cmd += ["--add-dir", d]
    try:
        p = sh(cmd, cwd, env=env, timeout=timeout)
        rc, out = p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        rc, out = 124, ""
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)
    info = {"rc": rc, "turns": None, "secs": round(time.time() - t0, 1), "reply": "", "subtype": None}
    try:
        j = json.loads(out)
        info.update(turns=j.get("num_turns"), reply=(j.get("result") or "")[:300], subtype=j.get("subtype"))
    except ValueError:
        info["reply"] = out[-300:]
    return info


def changed_files(cwd):
    p = _git(cwd, "status", "--porcelain", "-z", "-uall")  # -z: names are never quoted, entries end with NUL
    if p.returncode:
        return None
    out, items, i = [], p.stdout.split("\0"), 0
    while i < len(items):
        e = items[i]
        i += 1
        if len(e) > 3:
            out.append(e[3:])
        if e[:1] in ("R", "C") or e[1:2] in ("R", "C"):  # rename/copy: the old path is the next entry
            i += 1
    return sorted(out)


def cite_check(text, root):
    """Fraction of `path:line[-line]` cites that point at an existing file and a line inside it."""
    cites = set(re.findall(r"`?((?:[\w.\-]+/)*[\w.\-]+\.\w+):(\d+)(?:-(\d+))?`?", text))
    ok = bad = 0
    bad_list = []
    for path, a, b in cites:
        fp = os.path.join(root, path)
        if not os.path.isfile(fp):
            fp = os.path.join(root, path.lstrip("/"))
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                n = sum(1 for _ in fh)
            good = int(a) <= n and int(b or a) <= n
        except OSError:
            good = False
        ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
        if not good:
            bad_list.append(f"{path}:{a}")
    return ok, bad, bad_list[:5]


def _read_only_prompt(a, kind, out):
    if kind == "pack":
        goal = text_arg(a.goal)
        return (
            f"Build a context pack for a planner who will NOT explore the repo himself. Goal:\n{goal}\n\n"
            f"Read what you need with Read/Grep/Glob, then write ONE file {out} (max {a.max_words} words) with: "
            f"(1) goal and constraints in 5 lines; (2) the files that matter, each as path:line-range plus one line on why; "
            f"(3) at most 8 verbatim excerpts of 15 lines or less that the planner must see, each headed by its path:line-range; "
            f"(4) risks and open questions. Every claim carries a path:line cite you actually saw. No filler, no speculation. Reply with one line."
        )
    inputs = ", ".join(a.inputs)
    return (
        f"Task:\n{text_arg(a.instruction)}\n\nInput files (read them): {inputs}\n"
        f"Write the result to ONE file {out}. Be exact; quote, do not paraphrase numbers or names. You have {a.max_turns} turns: write the file by turn "
        f"{a.max_turns // 2} (even if incomplete), then complete it. Reply with one line."
    )


def _revert_strays(root, before, out):
    """Remove (untracked) or restore (tracked) everything the model touched besides OUT. -> the stray paths."""
    after = changed_files(root)
    stray = [] if before is None or after is None else [f for f in after if f not in before and f != out and not f.startswith(out + "/")]
    for f in stray:  # anything else the model touched is removed
        tracked = _git(root, "ls-files", "--error-unmatch", f).returncode == 0
        if tracked:
            _git(root, "checkout", "--", f)
        else:
            sh(["rm", "-rf", f], root)
    return stray


def _read_only_line(a, kind, out, root, res):
    """-> (ok, words, status line without the stray suffix)."""
    ok = os.path.isfile(out) and os.path.getsize(out) > 0
    words = 0
    if ok:
        with open(out, encoding="utf-8", errors="replace") as fh:
            words = len(fh.read().split())
    line = f"LOCALDEV {kind} {'OK' if ok else 'FAILED'} out={out} words={words} turns={res['turns']} secs={res['secs']}"
    if not ok:
        line += f" stop={res.get('subtype')} rc={res['rc']} reply={res['reply'][:120]!r}"
    if ok and kind == "pack":
        with open(out, encoding="utf-8", errors="replace") as fh:
            good, bad, bl = cite_check(fh.read(), root)
        line += f" cites={good}/{good + bad} valid" + (f" bad={bl}" if bad else "")
        if words > a.max_words * 1.3:
            line += " OVERSIZE"
    return ok, words, line


def read_only_task(a, kind):
    root = os.getcwd()
    out = os.path.relpath(os.path.abspath(a.out), root)
    if out.startswith(".."):
        sys.exit("localdev: --out must be inside the current directory")
    before = changed_files(root)
    prompt = _read_only_prompt(a, kind, out)
    allowed = ["Read", "Grep", "Glob", f"Edit(/{out})"]
    policy = load_policy()
    system, names = system_prompt(root, [kind], policy, explicit=getattr(a, "profile", None))
    res = run_agent(prompt, root, "Read,Grep,Glob,Edit,Write", allowed, max_turns=min(a.max_turns, policy["max_turns"]), system=system)
    stray = _revert_strays(root, before, out)
    ok, words, line = _read_only_line(a, kind, out, root, res)
    if stray:
        line += f" reverted-stray={stray}"
    print(line)
    log_run(root, {"mode": kind, "out": out, "ok": ok, "words": words, "turns": res["turns"], "secs": res["secs"], "profiles": names, "stray": stray})
    return 0 if ok else 1


ID_RE = re.compile(r"`?([A-Za-z_$][\w$]*)`?")
DEF_RE = re.compile(r"\b(def|class|function|fn|struct|enum|interface|type|const|let|var|func)\b|=>|=\s*function")


def goal_identifiers(goal):
    """Identifiers a goal names on purpose: camelCase, snake_case, `backticked`, or name(). Plain English words are not."""
    out = []
    for m in re.finditer(r"`([^`\s]+)`|([A-Za-z_$][\w$]*)\(\)?|\b([A-Za-z_$][\w$]*)\b", goal):
        w = m.group(1) or m.group(2) or m.group(3)
        w = (w or "").strip("`()")
        if len(w) < 4 or not re.fullmatch(r"[A-Za-z_$][\w$]*", w):
            continue
        if (m.group(1) or m.group(2) or re.search(r"[a-z][A-Z]|_|[A-Z]{2}", w) or re.search(r"\d", w)) and w not in out:
            out.append(w)
    return out[:8]


def rg_globs():
    """rg exclusion globs generated from ask_code.SKIP_DIRS, plus minified JS."""
    from search import ask_code

    return [f"--glob=!{d}" for d in sorted(ask_code.SKIP_DIRS)] + ["--glob=!*.min.js"]


def parse_json_obj(text):
    """First JSON object in TEXT (raw_decode from the first `{`); None when there is none."""
    return first_json_obj(text)


def identifier_hits(root, goal, per_ident=6):
    """Exact word hits (rg) for identifiers in the goal: definition-looking lines first. Never dropped by the judge."""
    hits = []
    for ident in goal_identifiers(goal):
        try:
            r = subprocess.run(["rg", "-nw", "--no-heading", *rg_globs(), "-m", "20", ident, root], capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        rows = []
        for line in r.stdout.splitlines():
            f, lineno, txt = ([*line.split(":", 2), "", ""])[:3]
            if lineno.isdigit():
                rows.append((0 if DEF_RE.search(txt) else 1, os.path.relpath(f, root), int(lineno)))
        for _, f, row_no in sorted(rows)[:per_ident]:
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            first, last = max(1, row_no - 2), min(len(lines), row_no + 12)
            hits.append({"file": f, "start": first, "end": last, "text": "\n".join(lines[first - 1 : last]), "exact": ident})
    return hits


def _pack_queries(strata, goal):
    """The model's search queries for GOAL (at most 8); when it gives nothing usable, the goal's own sentences."""
    ans = strata.strata_call(
        "A planner must write a task plan for the goal below and will NOT explore the code himself. List up to 8 short plain-words "
        "descriptions of the code or documents he must see, one behaviour or topic each (no file names). "
        + JSON_ONLY
        + '{"queries": ["..."]}\n\nGOAL:\n'
        + goal[:LOCAL_GOAL_CHARS]
    )
    try:
        queries = [str(x) for x in parse_json_obj(ans)["queries"]][:8]
    except (AttributeError, KeyError, TypeError, ValueError):  # no usable queries: the goal's own sentences follow
        queries = []
    if not queries:  # model gave nothing usable: fall back to the goal's own sentences
        queries = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", goal) if len(x.split()) >= 6][:8]
    return queries


def _pack_hits(ask_code, root, goal, queries):
    """Exact identifier hits first, then the search hits that do not overlap them; sorted by file and line."""
    hits = identifier_hits(root, goal)  # exact identifier hits first: never dropped
    for q in queries:
        for c in ask_code.query(root, q, k=2):
            if not any(h["file"] == c["file"] and abs(h["start"] - c["start"]) < ask_code.CHUNK for h in hits):
                hits.append(c)
    hits.sort(key=lambda c: (c["file"], c["start"]))
    return hits


def _merge_windows(hits):
    """Touching windows of one file become one excerpt."""
    merged: list = []
    for c in hits:
        if merged and merged[-1]["file"] == c["file"] and c["start"] <= merged[-1]["end"] + 1:
            if c.get("exact"):
                merged[-1]["exact"] = c["exact"]
            prev = merged[-1]
            L = prev["text"].splitlines() + c["text"].splitlines()[max(0, prev["end"] - c["start"] + 1) :]
            prev.update(end=c["end"], text="\n".join(L))
        else:
            merged.append(dict(c))
    return merged


def _pack_judge(strata, goal, merged):
    """-> (summary, why): the model's goal summary and one-line reason per excerpt index; ("", {}) when it answers nothing usable."""
    listing = "\n".join(f"[{i}] {c['file']}:{c['start']}-{c['end']}  " + " ".join(c["text"].split())[:160] for i, c in enumerate(merged))
    ans = strata.strata_call(
        "Goal:\n"
        + goal[:LOCAL_GOAL_SHORT_CHARS]
        + "\n\nExcerpts a planner will get:\n"
        + listing
        + "\n\n"
        + JSON_ONLY
        + '{"summary": "the goal and its constraints in at most 5 short lines", '
        '"why": {"0": "max 12 words: why excerpt 0 matters", ...}}',
        max_tokens=LOCAL_MAX_TOKENS,
    )
    try:
        j = parse_json_obj(ans)
        return str(j.get("summary", "")), {str(k): str(v) for k, v in (j.get("why") or {}).items()}
    except (AttributeError, KeyError, TypeError, ValueError):
        return "", {}


def _pack_body(merged, why, max_words):
    """Exact hits first (they survive the word cap), then cap. -> (merged in new order, why re-keyed to it, body blocks, words)."""
    order = sorted(range(len(merged)), key=lambda i: (not merged[i].get("exact"), i))
    merged = [merged[i] for i in order]
    why = {str(order.index(int(k))): v for k, v in why.items() if k.isdigit() and int(k) in order}
    body, words = [], 0
    for i, c in enumerate(merged):
        txt = "\n".join(c["text"].splitlines()[:60])
        w = len(txt.split())
        if words + w > max_words * 2 and not c.get("exact"):  # the cap counts excerpts at double weight: they are what costs the reader
            break
        words += w
        body.append(f"### {c['file']}:{c['start']}-{c['end']}" + (f" - {why[str(i)]}" if str(i) in why else "") + f"\n```\n{txt}\n```")
    return merged, why, body, words


def _pack_log(root, out, t0, ok, words):
    log_run(root, {"mode": "pack", "out": out, "ok": ok, "words": words, "turns": 0, "secs": round(time.time() - t0, 1), "profiles": ["search"], "stray": []})


def pack_task(a):
    """Search-driven context pack: the model proposes queries and judges relevance; CODE retrieves and copies the excerpts verbatim,
    so every cite is valid by construction and the run takes a minute, not thirty wandering turns."""
    sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]
    from search import ask_code, strata

    root = os.getcwd()
    out = os.path.relpath(os.path.abspath(a.out), root)
    if out.startswith(".."):
        sys.exit("localdev: --out must be inside the current directory")
    t0 = time.time()
    goal = text_arg(a.goal)
    ask_code.refresh(root, embed=0)  # incremental, keyword index only: no embedding model needed
    queries = _pack_queries(strata, goal)
    merged = _merge_windows(_pack_hits(ask_code, root, goal, queries))
    if not merged:
        print(f"LOCALDEV pack NOT FOUND: no identifier hit and no search hit for the goal (queries={len(queries)}). Use grep/nav directly.")
        _pack_log(root, out, t0, False, 0)
        return 1
    summary, why = _pack_judge(strata, goal, merged)
    merged, why, body, words = _pack_body(merged, why, a.max_words)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    index = "\n".join(f"- {c['file']}:{c['start']}-{c['end']}" + (f" - {why[str(i)]}" if str(i) in why else "") for i, c in enumerate(merged[: len(body)]))
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(
            f"Starting points, not exhaustive; verify with grep/nav.\n# Context pack (built by the local model; excerpts are verbatim)\n\n## Goal\n{summary or goal[:600]}\n\n"
            f"## Where to look\n{index}\n\n## Excerpts\n" + "\n\n".join(body) + "\n"
        )
    good, bad, _ = cite_check(Path(a.out).read_text(encoding="utf-8"), root)
    print(
        f"LOCALDEV pack OK out={out} queries={len(queries)} excerpts={len(body)}/{len(merged)} words={words} cites={good}/{good + bad} valid secs={round(time.time() - t0, 1)}"
    )
    _pack_log(root, out, t0, True, words)
    return 0


def changed_vs(base, wt):
    """Paths that differ from the base commit: committed, staged, unstaged and untracked (bytecode excluded)."""
    tracked = _git(wt, "diff", "--name-only", "-z", base).stdout.split("\0")  # -z: no quoting of non-ASCII names
    untracked = _git(wt, "ls-files", "-z", "--others", "--exclude-standard").stdout.split("\0")
    return sorted({f for f in tracked + untracked if f and "__pycache__" not in f})


def _code_settings(a, hdr, policy, root):
    """Fill a.test / a.paths from the task header and check the commands and read dirs against the policy. -> (extra_cmds, read_dirs)."""
    a.test = a.test or hdr.get("test")
    a.paths = a.paths or hdr.get("paths")
    if not a.test or not a.paths:
        sys.exit("localdev: need a test command and predicted paths (flags or 'Predicted paths:' / 'Test (loop):' lines in the task file)")
    extra_cmds = [c.strip() for c in re.split(r"\s*;\s*", hdr.get("run_also", "")) if c.strip()]
    for c in [a.test, *extra_cmds]:
        if not command_ok(c, policy):
            sys.exit(f"localdev: command not allowed by localdev/policy.json: {c!r}")
    read_dirs = [os.path.abspath(os.path.join(root, d.strip())) for d in hdr.get("read_also", "").split(",") if d.strip()]
    if len(read_dirs) > policy["max_read_also_dirs"] or any(not os.path.isdir(d) for d in read_dirs):
        sys.exit("localdev: 'Read also' has too many directories or one does not exist")
    return extra_cmds, read_dirs


def _code_prompt(task_rel, task_paths, test):
    return (
        f"Do the task in {task_rel}. Edit only these paths: {', '.join(task_paths)}. Run exactly this test command to check your work: {test}\n"
        f"Never edit tests. When it passes you may `git add` and `git commit` your change here (this is your own branch). "
        f"Reply with one line: DONE or what blocks you."
    )


def _code_allowlist(task_paths, test, extra_cmds, policy):
    return [
        "Read",
        "Grep",
        "Glob",
        *[f"Edit(/{x})" for x in task_paths],
        f"Bash({test}:*)",
        "Bash(git status:*)",
        "Bash(git diff:*)",
        "Bash(git log:*)",
        *[f"Bash({g}:*)" for g in policy["code_mode_git"]],
        *[f"Bash({c}:*)" for c in extra_cmds],
    ]


def _revert_out_of_scope(base, wt, task_rel, task_paths):
    """Restore anything outside the predicted paths, committed or not. -> the reverted paths."""
    viol = []
    for f in changed_vs(base, wt):
        if f == task_rel or any(f == x or f.startswith(x.rstrip("/") + "/") for x in task_paths):
            continue
        viol.append(f)
        if _git(wt, "cat-file", "-e", f"{base}:{f}").returncode == 0:
            _git(wt, "checkout", base, "--", f)
        else:
            _git(wt, "rm", "-rf", "-q", "--cached", "--ignore-unmatch", f)
            sh(["rm", "-rf", f], wt)
    if _git(wt, "cat-file", "-e", f"{base}:{task_rel}").returncode != 0:  # the task copy is not part of the result
        _git(wt, "rm", "-q", "--cached", "--ignore-unmatch", task_rel)
    return viol


def _commit_leftovers(wt, cid, task_rel, task_paths):
    """Leave a clean branch ready to merge. -> True when a commit was made."""
    _git(wt, "add", "-A", "--", *task_paths)
    if _git(wt, "diff", "--cached", "--quiet").returncode == 0:
        return False
    c = _git(wt, "-c", "user.name=localdev", "-c", "user.email=localdev@local", "commit", "-qm", f"localdev {cid}: {os.path.basename(task_rel)}")
    return c.returncode == 0


def _code_report(rep, cid, verdict, branch, base, files, viol, test, t, res):
    with open(rep, "w", encoding="utf-8") as fh:
        fh.write(
            f"# localdev {cid}: {verdict}\nbranch {branch}, base {base[:10]}\nfiles: {files}\nreverted (outside the task paths): {viol}\n"
            f"test `{test}` exit {t.returncode}\nturns {res['turns']}, secs {res['secs']}\nagent said: {res['reply']}\n\n"
            f"## test output (tail)\n{(t.stdout + t.stderr)[-1500:]}\n"
        )


def _code_setup(a, root, wt, task_rel):
    """Read the task header and policy, copy the task file into WT, build the allowlist and system prompt. Returns (hdr, policy, task_paths, allowed, system, names, read_dirs)."""
    hdr = parse_header(Path(os.path.join(root, task_rel)).read_text(encoding="utf-8"))
    policy = load_policy()
    extra_cmds, read_dirs = _code_settings(a, hdr, policy, root)
    os.makedirs(os.path.dirname(os.path.join(wt, task_rel)), exist_ok=True)
    with open(os.path.join(wt, task_rel), "w", encoding="utf-8") as fh:
        fh.write(Path(os.path.join(root, task_rel)).read_text(encoding="utf-8"))
    task_paths = [x.strip() for x in a.paths.split(",") if x.strip()]
    allowed = _code_allowlist(task_paths, a.test, extra_cmds, policy)
    system, names = system_prompt(wt, [], policy, explicit=a.profile or hdr.get("profiles"), task_paths=task_paths)
    return hdr, policy, task_paths, allowed, system, names, read_dirs


def _code_test(test, wt):
    """Run the task test in WT (the authoritative run). A timeout or missing binary is a failed run with a message, not an error."""
    try:
        return sh(shlex.split(test), wt, timeout=LONG_CMD_TIMEOUT_S, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    except (subprocess.TimeoutExpired, OSError) as e:  # timeout or missing binary: a failed run with a report, not a traceback
        return subprocess.CompletedProcess(test, 1, stdout="", stderr=f"test run failed: {e}")


def code_task(a):
    """Run one code task in its own git worktree and branch: agent edits, test run, commit on PASS. Returns 0 on PASS, 1 otherwise."""
    root = os.path.abspath(a.repo)
    cid = a.id or time.strftime("%H%M%S")
    base_dir = os.path.join(root, WORK_DIR, "localdev", cid)
    wt = os.path.join(base_dir, "wt")
    branch = f"localdev/{cid}"
    os.makedirs(base_dir, exist_ok=True)
    p = _git(root, "worktree", "add", "-b", branch, wt, "HEAD")
    if p.returncode:
        sys.exit("localdev: git worktree failed (id already used?): " + p.stderr.strip()[:200])
    base = _git(wt, "rev-parse", "HEAD").stdout.strip()
    task_rel = os.path.relpath(os.path.abspath(a.task), root)
    try:  # every sys.exit below must not leave the worktree and branch behind
        hdr, policy, task_paths, allowed, system, names, read_dirs = _code_setup(a, root, wt, task_rel)
    except SystemExit:
        _git(root, "worktree", "remove", "--force", wt)
        _git(root, "branch", "-D", branch)
        raise
    res = run_agent(
        _code_prompt(task_rel, task_paths, a.test),
        wt,
        "Read,Grep,Glob,Edit,Write,Bash",
        allowed,
        max_turns=min(hdr.get("turns", a.max_turns), policy["max_turns"]),
        system=system,
        add_dirs=read_dirs,
    )
    viol = _revert_out_of_scope(base, wt, task_rel, task_paths)
    t = _code_test(a.test, wt)
    files = [f for f in changed_vs(base, wt) if f != task_rel]
    verdict = PASS if t.returncode == 0 and files else FAIL
    committed = _commit_leftovers(wt, cid, task_rel, task_paths) if verdict == PASS else False
    rep = os.path.join(base_dir, "report.md")
    _code_report(rep, cid, verdict, branch, base, files, viol, a.test, t, res)
    print(
        f"LOCALDEV code {verdict} id={cid} files={files} test={'OK' if t.returncode == 0 else 'FAIL'} turns={res['turns']} secs={res['secs']}"
        + (f" reverted={viol}" if viol else "")
    )
    print(f"branch {branch} (worktree {wt}, {'committed' if verdict == 'PASS' else 'left as is for inspection'})   report: {os.path.relpath(rep, root)}")
    log_run(
        root,
        {
            "mode": "code",
            "id": cid,
            "verdict": verdict,
            "files": files,
            "reverted": viol,
            "test_ok": t.returncode == 0,
            "turns": res["turns"],
            "secs": res["secs"],
            "profiles": names,
            "task": task_rel,
            "branch": branch,
            "committed": committed,
        },
    )
    return 0 if verdict == PASS else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--out", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--max-words", type=int, default=700)
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--profile")
    b = sub.add_parser("bulk")
    b.add_argument("--out", required=True)
    b.add_argument("--instruction", required=True)
    b.add_argument("--max-turns", type=int, default=30)
    b.add_argument("--profile")
    b.add_argument("inputs", nargs="+")
    c = sub.add_parser("code")
    c.add_argument("--task", required=True)
    c.add_argument("--test")
    c.add_argument("--profile")
    c.add_argument("--paths")
    c.add_argument("--id")
    c.add_argument("--repo", default=".")
    c.add_argument("--max-turns", type=int, default=30)
    g = sub.add_parser("log")
    g.add_argument("--last", type=int, default=10)
    a = ap.parse_args(argv)
    if a.mode == "log":
        fp = os.path.join(WORK_DIR, "localdev", "runs.jsonl")
        rows = []
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh]
        for r in rows[-a.last :]:
            print(
                f"{r['ts']} {r['mode']:<5} {r.get('verdict') or ('OK' if r.get('ok') else 'FAILED'):<6} turns={r.get('turns')} secs={r.get('secs')} profiles={','.join(r.get('profiles', []))} {r.get('task') or r.get('out', '')}"
            )
        print(f"{len(rows)} runs logged")
        return 0
    from search.strata import strata_up

    if not strata_up():
        print(f"skipped: local model down or off ({a.mode}); local-model steps are optional")
        return 0
    if a.mode == "code":
        return code_task(a)
    return pack_task(a) if a.mode == "pack" else read_only_task(a, a.mode)


if __name__ == "__main__":
    sys.exit(main())
