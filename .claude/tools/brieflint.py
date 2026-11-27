#!/usr/bin/env python3
"""brieflint: pre-flight lint for a task file. Quiet: 'OK' or one line per problem. Exit 1 on problems."""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from pipeline.configuration import AGENTS_DIR, JQ_FILE_MAX_BYTES, QRUN_CHAIN_READ_ONLY, QRUN_PATTERN
from pipeline.plan_files import paths_in
from pipeline.plan_rules import main_checkout_problems, untracked_problems

OWNER_CITE_RE = re.compile(r"\bA[1-5]\b|(?i:\bowner\b)(?!\s*:)")  # an owner label (A1-A5) or the word `owner` in prose; not an `owner:` header
JQ_RE = re.compile(r"\bjq\b(?:\s+-\S+)*\s+(?:'([^']*)'|\"([^\"]*)\")\s+([\w./-]+\.json)\b")
JQ_CHAIN_RE = re.compile(r"\s*((?:\.[^\s.\[\]|,()\"']+)+)")  # the leading `.a.b` chain of a filter
JQ_KEY_RE = re.compile(r"[A-Za-z_]\w*$")
JQ_STRING_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
QRUN_HEAD_RE = re.compile(rf"{QRUN_PATTERN}\s+--(?=\s|$)")  # a call that opens with qrun and its `--`
CHAIN_OPS_RE = re.compile(r"[;|]")  # shell operators: qrun runs no shell, so a Test line never uses them
READ_ONLY_TAIL_RE = re.compile(r"[<>`]|\$\(")  # a redirect or a substitution is not a read-only tail


def _missing_jq_key(filt, data):
    """The first key of the leading dotted chain of FILT (`.a.b`) that DATA (a dict tree) lacks, else None. Only a filter without a pipe is checked; strings are dropped first; the walk stops at a non-dict or at a key that is not a plain name (`-`, leading digit, `?`)."""
    filt = JQ_STRING_RE.sub('""', filt)
    m = None if "|" in filt else JQ_CHAIN_RE.match(filt)
    if not m:
        return None
    cur, walked = data, ""
    for key in m[1].split(".")[1:]:
        if not isinstance(cur, dict) or not JQ_KEY_RE.match(key):
            break
        walked += "." + key
        if key not in cur:
            return walked
        cur = cur[key]
    return None


def _jq_problems(t, repo):
    for m in JQ_RE.finditer(t):
        root = os.path.realpath(repo)
        f = os.path.realpath(os.path.join(root, m[3]))
        if os.path.commonpath([root, f]) != root:
            continue  # absolute or `..` path outside the repo
        try:
            if os.path.getsize(f) > JQ_FILE_MAX_BYTES:
                continue
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # unreadable or not yet created: nothing to compare
        miss = _missing_jq_key(m[1] or m[2], data)
        if miss:
            yield f"Test jq path {miss} does not exist in {m[3]} (jq prints null and exits 0 or fails): check the key"


def _test_line_problems(text, repo):
    p = []
    for m in re.finditer(r"(?m)^Test:\s*(.*)$", text):
        if re.search(r"(?<![\w-])grep\s+(?:-\w*\s+)*-\w*c", m[1]) and not re.search(r"!\s*grep", m[1]):
            p.append("Test uses `grep -c` (exits 1 on zero matches, so a green check fails): use `! grep -q PATTERN file` or compare the count")
        if repo:
            p += [*_jq_problems(m[1], repo), *main_checkout_problems(m[1], repo)]
    return p


def _untracked_problems(text, repo, git):
    """Predicted paths that are untracked or git-ignored in REPO (GIT: the stage's git_state, else computed). No repo: nothing to check."""
    return untracked_problems(paths_in(text), repo, git) if repo else []


def _chain_problems(t):
    """Chain checks for one Test: line, quotes stripped. No `;` or `|`; the first `&&` segment is `cs-qrun --`;
    each later segment is `cs-qrun --` or a plain read-only command."""
    if CHAIN_OPS_RE.search(t):
        return ["Test line has ; or | (qrun runs no shell; the agents' sandbox denies chains): one cs-qrun command, or cs-qrun -- cmd && read-only cmd"]
    first, *rest = t.split("&&")
    if not QRUN_HEAD_RE.match(first.strip()):
        return ["Test line does not start with cs-qrun -- (the test must run through qrun): write Test: cs-qrun -- cmd"]
    for seg in map(str.strip, rest):
        words = seg.split()
        if QRUN_HEAD_RE.match(seg) or (words and words[0] in QRUN_CHAIN_READ_ONLY and not READ_ONLY_TAIL_RE.search(seg)):
            continue
        return ["Test line chains with && a command that is not cs-qrun or read-only (qrun runs no shell): wrap the chain as qrun -- bash -c '...'"]
    return []


def _test_prefix_problems(text, repo):
    p = []
    for m in re.finditer(r"(?m)^Test:\s*(.*)$", text):
        # quoted text is a shell script or one literal argument, not a chain here: strip it once for the checks below
        t = re.sub(r"'[^']*'|\"[^\"]*\"", "", m[1])
        if re.match(r"[A-Z_][A-Z0-9_]*=", t) or re.search(r"(?:^|\s)env\s|\bcd\s", t):
            p.append("Test line has a VAR= prefix, env or cd (the agents' sandbox denies it): one command through qrun, qrun applies test_env")
        p += _chain_problems(t)
        if any(re.fullmatch(r"[\w./-]*\*[\w./*-]*", tok) for tok in t.split()):
            p.append("Test line has an unquoted glob (qrun runs no shell, so it is never expanded): name the directory or file")
        p += main_checkout_problems(m[1], repo)
    return p


def _runner_problems(text):
    """Problems with the test command and the ops runner: qrun use, Device line for ASR/harvest, no tail/head pipe."""
    p = []
    ops = re.search(r"(?im)^owner:\s*(ops|general-purpose)|\(ops\)", text)
    if not ops and not re.search(QRUN_PATTERN, text):
        p.append("no qrun.py or cs-qrun test command (Loop/Done must use qrun)")
    if ops and re.search(r"(?i)FRNUM_|transcri|harvest", text) and not re.search(r"(?im)^device:\s*(cuda|cpu|auto)\b", text):
        p.append('ops brief runs ASR/harvest but has no "Device: cuda|cpu|auto" line')
    if re.search(r"\|\s*(tail|head)\b", text):
        p.append("test/command piped through tail/head")
    return p


def _anchor_problem(text):
    """The no-anchor message; names the `Pattern:` path that lacks its line, with the grep that finds it."""
    m = re.search(r"(?im)^\W*pattern:\s*`?([\w./-]+\.\w+)`?\s*$", text)
    if not m:
        return "no file:line anchor (agent will explore; paste path:line)"
    return f"no file:line anchor: `Pattern: {m[1]}` has no line; find it with `grep -n <symbol> {m[1]}` and write `Pattern: {m[1]}:<line>`"


def _brief_problems(text):
    """Problems with the brief's shape: predicted paths, Done line, file:line anchor, absolute cd, owner cite without a path."""
    p = []
    if not re.search(r"(?i)\bfiles\b[^\n]*(only|allow)|--allow|predicted[ -]paths", text):
        p.append('no predicted-paths line ("Files (only these)" or --allow)')
    if not re.search(r"(?i)\bdone( when)?\b\s*:", text):
        p.append('no "Done:" criteria line')
    if not re.search(r"[\w./-]+\.\w+:\d+", text):
        p.append(_anchor_problem(text))
    if re.search(r"(?<![\w.])cd /(?!root/\.claude)\S+", text):
        p.append("absolute cd in command (use paths relative to repo root)")
    if any(OWNER_CITE_RE.search(ln) and not re.search(r"[\w./-]+\.\w+", ln) for ln in text.splitlines()):
        p.append('cites an owner label or "owner" with no owner text file path: give the path of the owner text on that line')
    return p


def lint(text, repo=None, git=None):
    """Brief problems as a list of messages; empty when the brief passes."""
    p = _untracked_problems(text, repo, git)
    p += _runner_problems(text)
    p += _brief_problems(text)
    p += _test_prefix_problems(text, repo)
    p += _test_line_problems(text, repo)
    if not re.search(r"(?i)max turns|turn budget|turns?\s*(?:[<=≤]+|:)\s*\d+", text):
        p.append('no turn budget (add "turns <= N")')
    if len(text) > 6000:
        p.append(f"brief > 6000 chars ({len(text)}); move reference text to a file slice")
    return p


def is_agent_file(path):
    return os.path.realpath(path).startswith(os.path.join(AGENTS_DIR, ""))


if __name__ == "__main__":
    bad = 0
    for f in sys.argv[1:]:
        if is_agent_file(f):
            continue
        for m in lint(Path(f).read_text(encoding="utf-8")):
            print(f"{f}: {m}")
            bad = 1
    if not bad:
        print("OK")
    sys.exit(bad)
