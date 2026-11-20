"""CI drift check: commands the project's CI runs that no `gates` entry in pipeline.json covers. Line-based, no YAML parser."""

import glob
import os
import re

from .configuration import all_gates_of, repo_dir

RUN_RE = re.compile(r"^\s*(?:-\s+)?run:\s*(.*?)\s*$")
SETUP_ONLY = ("npm ci", "npm install")


def _norm(cmd):
    return " ".join(cmd.split())


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
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                if lines[i].strip():
                    out.append(lines[i].strip())
                i += 1
        elif val:
            out.append(val.strip("\"'") if val[:1] in "\"'" and val[-1:] == val[:1] else val)
    return out


def drift(project_dir, cfg):
    """-> [warning]: CI commands missing from the gates (`&&` parts count). Never raises on config; `ci_drift: false` disables."""
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
    known = set()
    for g in gates:
        known.add(_norm(g["cmd"]))
        known.update(_norm(p) for p in g["cmd"].split("&&") if p.strip())
    warns = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for cmd in ci_run_lines(fh.read()):
                n = _norm(cmd)
                if n.startswith("echo ") or n in SETUP_ONLY or n in known or all(_norm(q) in known for q in n.split("&&")):
                    continue
                warns.append(f"CI runs `{n}` ({os.path.basename(f)}) but no gate covers it")
    return warns
