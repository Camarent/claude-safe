"""Shared helpers: jsonl, atomic text write, clock, stage paths, json-line print, tool subprocess."""

import argparse
import contextlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time

from pipeline.configuration import (
    ENV_SCRIPT_TIMEOUT,
    EST_ASCII_CHARS_PER_TOKEN,
    EV_DISPATCH,
    EV_REPORT,
    EV_RETRY,
    EV_SKIP,
    HOME,
    ORCH_LOG,
    TASKS_FILE,
    WORK_DIR,
    tool_setting,
)

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tools dir of this checkout


def now() -> float:
    return time.time()


def paths(a) -> tuple[str, str]:
    """-> (stage dir, orch.jsonl path) for any object with .dir and .stage."""
    w = os.path.join(a.dir, WORK_DIR, a.stage)
    if not os.path.exists(os.path.join(w, TASKS_FILE)):
        import glob

        stray = glob.glob(os.path.join(a.dir, "*", WORK_DIR, a.stage, TASKS_FILE))
        if stray:
            sys.exit(f"stage plan found in {os.path.dirname(stray[0])}, not in {w}: move it there (stray <repo>/.work copy)")
    return w, os.path.join(w, ORCH_LOG)


def work_subdir(name) -> str:
    """Subdir of .work/<stage>/ that holds file `name` ("" = top level)."""
    b = os.path.basename(name)
    if b.startswith("report-"):
        return "reports"
    if b.startswith("leads-"):
        return "leads"
    if b.startswith(("audit-", "retro")) or b in ("scans.md", "tools-check.md", "plan-vs-changes.md", "incidents.json"):
        return "retro" if b.startswith("retro") or b == "incidents.json" else "audits"
    return ""


def work_path(stage_dir, name, create=False) -> str:
    """Path of file `name` in a stage dir by layout; create=True makes the subdir (writers). Readers fall back to the old top-level path when only that exists."""
    sub = work_subdir(name)
    new = os.path.join(stage_dir, sub, name)
    if create:
        os.makedirs(os.path.dirname(new), exist_ok=True)
        return new
    old = os.path.join(stage_dir, name)
    if sub and not os.path.exists(new) and os.path.exists(old):
        return old
    return new


def parse_jsonl(lines, dicts_only=False) -> tuple[list, list[int]]:
    """-> (records, numbers of the unreadable lines) for LINES of JSONL. Blank lines are skipped; dicts_only drops other JSON values (silently)."""
    out, bad = [], []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            bad.append(n)
            continue
        if not dicts_only or isinstance(rec, dict):
            out.append(rec)
    return out, bad


def read_jsonl(path, quiet=False, dicts_only=True, errors="strict") -> list:
    """-> the records of a JSONL file ([] when missing). Blank lines are skipped; a line that is not JSON (a truncated last line after a crash) is skipped with one stderr warning unless QUIET.
    dicts_only (default) keeps only JSON objects: every log the pipeline writes holds objects, and a stray number or list must not reach `e["ev"]`; errors is the text decoding policy ("replace" tolerates bad bytes)."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors=errors) as f:
        out, bad = parse_jsonl(f, dicts_only)
    if bad and not quiet:
        sys.stderr.write(f"warning: {path}: skipped {len(bad)} unreadable line(s), first at line {bad[0]}\n")
    return out


def scan_json(text, opens="{", line_start=False) -> tuple[bool, object]:
    """-> (found, value): the first JSON document in TEXT that starts at a char of OPENS (at a line start when LINE_START); raw_decode from each candidate."""
    dec = json.JSONDecoder()
    pat = re.compile(f"^[{re.escape(opens)}]" if line_start else f"[{re.escape(opens)}]", re.M)
    for m in pat.finditer(text):
        try:
            return True, dec.raw_decode(text[m.start() :])[0]
        except ValueError:
            continue
    return False, None


def first_json_obj(text, greedy=False) -> dict | None:
    """-> the first JSON object (dict) in TEXT, or None. greedy=True: parse the span from the first `{` to the last `}` only (a model answer with one object); else raw_decode from each `{`."""
    text = text or ""
    if greedy:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            obj = json.loads(m.group(0)) if m else None
        except ValueError:
            return None
        return obj if isinstance(obj, dict) else None
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj = dec.raw_decode(text[m.start() :])[0]
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def est_tokens(text) -> int:
    """Conservative token estimate of `text`: ASCII chars / 3.5, every other char (Japanese, Cyrillic ...) as one token."""
    ascii_chars = sum(1 for c in text if c < "\x80")
    return int(ascii_chars / EST_ASCII_CHARS_PER_TOKEN) + len(text) - ascii_chars


def text_arg(v) -> str:
    """V is `@file` (the file's text) or a literal."""
    if v.startswith("@"):
        with open(v[1:], encoding="utf-8") as f:
            return f.read()
    return v


def append_jsonl(path, rec) -> None:
    """Append one record as one `write` on an O_APPEND descriptor: concurrent writers (the driver and `orch report`) cannot interleave inside a line."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lead = ""
    if os.path.isfile(path) and os.path.getsize(path):  # a torn earlier write left no newline: end that line first
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                lead = "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        os.write(fd, (lead + json.dumps(rec) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def swallowed(where, exc) -> None:
    """Best-effort paths swallow their errors by design; leave one stderr line so a hidden bug can still be seen."""
    print(f"swallowed in {where}: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)


LOCAL_LOG_MAX_BYTES = 5 * 1024 * 1024  # past this the log moves to `.1` (one generation kept)


def local_log(msg) -> None:
    """One timestamped line about a local-model call (request, answer time, slot wait) in $LOCALMODEL_LOG, else <tools home>/logs/localmodel.log. Never printed, never raises."""
    path = os.environ.get("LOCALMODEL_LOG") or os.path.join(HOME, "logs", "localmodel.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOCAL_LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} {os.path.basename(sys.argv[0])}: {msg}\n")
    except OSError:
        pass


def append_event(log, ev) -> None:
    append_jsonl(log, {**ev, "t": now()})


def read_text(path, default="") -> str:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return f.read()


NEW_FILE_MODE = 0o644  # mode of a file write_text creates (an existing file keeps its mode)


def write_text(path, text) -> None:
    """Atomic and durable: write a uniquely named temp file next to `path`, fsync it, then rename. Two writers of one path (threads included) never share a temp file, and a crash leaves the old file or the new one, not an empty one."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else NEW_FILE_MODE)  # mkstemp files are 0600
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def jprint(obj) -> None:
    print(json.dumps(obj))


def run_py(script, *args, cwd=None, timeout=None) -> subprocess.CompletedProcess[str]:
    """Run `python3 -I tools/<script> <args>` capturing text output. -> CompletedProcess."""
    return subprocess.run([sys.executable, "-I", os.path.join(TOOLS, script), *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def script_timeout(default=None) -> float:
    """Seconds one script step may run: env ORCH_SCRIPT_TIMEOUT, else DEFAULT (the SCRIPT_TIMEOUT_S tool setting when None: pipeline.json tool_settings.script_timeout_s of the project at os.getcwd(), the driver runs from the workspace root). A bad value, 0, a negative, nan (nan > 0 is False) and inf (no finite wait for the OS timer) give DEFAULT."""
    default = float(tool_setting("SCRIPT_TIMEOUT_S") if default is None else default)
    try:
        v = float(os.environ.get(ENV_SCRIPT_TIMEOUT, default))
    except ValueError:
        return default
    return v if math.isfinite(v) and v > 0 else default


def run_capture(args, cwd=None, timeout=None, env=None, swallow=False, **kw) -> subprocess.CompletedProcess[str] | None:
    """subprocess.run with captured text output (check=False) -> CompletedProcess. swallow=True: -> None when the command cannot run or times out; else those errors raise."""
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env, check=False, **kw)
    except (OSError, subprocess.SubprocessError):
        if not swallow:
            raise
        return None


def default_dir() -> str:
    """Default for `--dir`: the nearest dir upward from the cwd that holds pipeline.json, else `.`. An agent that `cd`s into a nested repo still reaches the workspace's `.work`."""
    d = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(d, "pipeline.json")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return "."
        d = up


def unreported(evs) -> list[str]:
    """Ids of dispatches that got neither a report nor a later retry/skip/dispatch of the same task (a newer dispatch supersedes it)."""
    reported = {e["id"] for e in evs if e["ev"] == EV_REPORT}

    def cleared(i, e) -> bool:  # a later retry/skip/dispatch of the same task settles the dispatch
        return any(x["ev"] in (EV_RETRY, EV_SKIP, EV_DISPATCH) and x.get("task") == e.get("task") for x in evs[i + 1 :])

    return [e["id"] for i, e in enumerate(evs) if e["ev"] == EV_DISPATCH and e["id"] not in reported and not cleared(i, e)]


# re-exported: callers and tests import them from here
from pipeline.stagedir import STAGE_NAME_RE, stage_root  # noqa: E402, F401


def add_common_args(ap, dir_default=".", stage=False, dir_required=False) -> None:
    """The `--dir` (and, with STAGE, the required `--stage`) options most step scripts share."""
    if dir_required:
        ap.add_argument("--dir", required=True)
    else:
        ap.add_argument("--dir", default=dir_default)
    if stage:
        ap.add_argument("--stage", required=True)


def add_subcommand(sub, name, fn, stage_required=True) -> argparse.ArgumentParser:
    """Argparse boilerplate of both CLIs: a subcommand with --stage and --dir."""
    p = sub.add_parser(name)
    p.add_argument("--stage", required=stage_required, default="" if not stage_required else None)
    p.add_argument("--dir", default=default_dir())
    p.set_defaults(fn=fn)
    return p
