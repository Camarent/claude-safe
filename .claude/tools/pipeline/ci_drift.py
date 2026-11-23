"""CI drift check: commands the project's CI runs that no `gates` entry in pipeline.json covers. Line-based, no YAML parser.

Rule: a CI run line is drift unless a gate covers it, `ci_drift_ignore` names it, or it is never run locally: a line that references a GitHub-only variable (RUNNER_*, GITHUB_*), a secret or a `${{ }}` expression, or a bare shell setting (`set ...`, `cd X`, `export X`, `VAR=value`). The patterns are in configuration/pr_gates.py."""

import glob
import os
import re

from .configuration import all_gates_of, repo_dir
from .configuration.pr_gates import CI_ONLY_RE, CI_SHELL_SETTING_RE

RUN_RE = re.compile(r"^\s*(?:-\s+)?run:\s*(.*?)\s*$")
SETUP_ONLY = ("npm ci", "npm install")
BLOCK_OPEN = {"if": "fi", "for": "done", "while": "done", "until": "done", "case": "esac"}
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def _norm(cmd):
    return " ".join(cmd.split())


def _logical(block):
    """-> [command] from the lines of one `run:` block: backslash continuations are joined; comments, heredoc bodies and compound statements (if/for/while/case ... fi/done/esac) are not single commands and are skipped."""
    out, cur, closers, heredoc = [], "", [], None
    for raw in block:
        line = raw.strip()
        if heredoc:
            heredoc = None if line == heredoc else heredoc
            continue
        if not line or (line.startswith("#") and not cur):
            continue
        cur = f"{cur} {line}" if cur else line
        if cur.endswith("\\"):
            cur = cur[:-1].rstrip()
            continue
        m = HEREDOC_RE.search(cur)
        if m:
            heredoc = m.group(2)
        tokens = re.split(r"[;\s]+", cur)
        closer = BLOCK_OPEN.get(tokens[0])
        if closer:
            if closer not in tokens[1:]:
                closers.append(closer)
        elif closers and tokens[0] == closers[-1]:
            closers.pop()
        elif not closers:
            out.append(cur)
        cur = ""
    return out


def ci_run_lines(yml_text):
    """-> [command] of every `run:` entry: a single-line value, or each non-empty line of a `run: |` / `run: >` block."""
    out, lines, i = [], yml_text.splitlines(), 0
    while i < len(lines):
        m = RUN_RE.match(lines[i])
        i += 1
        if not m:
            continue
        val = m.group(1)
        if val[:1] in ("|", ">"):
            indent = len(lines[i - 1]) - len(lines[i - 1].lstrip())
            block = []
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                block.append(lines[i])
                i += 1
            out.extend(_logical(block))
        elif val:
            out.append(val.strip("\"'") if val[:1] in "\"'" and val[-1:] == val[:1] else val)
    return out


def drift(project_dir, cfg):
    """-> [problem]: CI commands missing from the gates (`&&` parts count). Never raises on config; `ci_drift: false` disables. `ci_drift_ignore` lists CI-only commands (setup, CI-only jobs) by exact text or leading words."""
    if cfg.get("ci_drift") is False:
        return []
    files = sorted(glob.glob(os.path.join(repo_dir(project_dir, cfg), ".github", "workflows", "*.yml")))
    if not files:
        return []
    try:
        gates = all_gates_of(cfg)
    except ValueError:
        gates = []
    if not gates:
        return ["no gates configured; CI drift not checked"]
    return [f"CI runs `{n}` ({os.path.basename(f)}) but no gate covers it" for f, n in _uncovered(files, gates, cfg)]


def _covered(n, known):
    """-> True when the normalised CI command N is a gate command, a part of one, a setup step or an echo."""
    return n.startswith("echo ") or n in SETUP_ONLY or n in known or all(_norm(q) in known for q in n.split("&&"))


def _never_local(n):
    """-> True when the normalised CI command N only runs on GitHub (GitHub-only variables, secrets) or is a bare shell setting."""
    return bool(CI_ONLY_RE.search(n) or CI_SHELL_SETTING_RE.fullmatch(n))


def _uncovered(files, gates, cfg):
    """-> [(workflow file, command)] of the CI run lines in FILES that no gate in GATES covers and `ci_drift_ignore` does not name."""
    ignore = [_norm(i) for i in cfg.get("ci_drift_ignore", [])]
    known = set()
    for g in gates:
        known.add(_norm(g["cmd"]))
        known.update(_norm(p) for p in g["cmd"].split("&&") if p.strip())
    out = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for cmd in ci_run_lines(fh.read()):
                n = _norm(cmd)
                if _covered(n, known) or _never_local(n) or any(n == i or n.startswith(i + " ") for i in ignore):
                    continue
                out.append((f, n))
    return out


def ci_commands(repo, cfg):
    """-> [command] the CI workflows of REPO run that no `gates` entry covers and `ci_drift_ignore` does not name (the lines pr-ready runs beyond the gates). Same reading as drift()."""
    files = sorted(glob.glob(os.path.join(repo, ".github", "workflows", "*.yml")))
    out = []
    for _, n in _uncovered(files, all_gates_of(cfg), cfg):
        if n not in out:
            out.append(n)
    return out
