"""Docs impact: a doc that cites a file or a top-level def the job diff changes must change too.

Pure: the diff text, the doc paths and the project settings come in as arguments; the doc text is read from the worktree. The changed paths come from the diff headers (cite_check._header_file), the symbols from the changed hunks (a column-0 def or class line, or the function context of a hunk header)."""

import fnmatch
import os
import re
from pathlib import Path

from . import cite_check
from .configuration import (
    DOCS_IMPACT_DEF_RE,
    DOCS_IMPACT_DEFAULT,
    DOCS_IMPACT_EXEMPT_KEY,
    DOCS_IMPACT_GENERIC_SYMBOLS,
    DOCS_IMPACT_HEADER_RE,
    DOCS_IMPACT_PATH_RE,
    DOCS_IMPACT_STAGE_DEFAULT_KEY,
    DOCS_IMPACT_SYMBOL_MIN,
    DOCS_IMPACT_TICK_RE,
)


def _def_name(text):
    """The def or class name `text` starts with, or None."""
    d = DOCS_IMPACT_DEF_RE.match(text)
    return d[1] if d else None


def _header_def(ln):
    """The def or class name of a hunk header (its section text), or None."""
    m = DOCS_IMPACT_HEADER_RE.match(ln)
    return _def_name(m[1]) if m else None


def _changes(diff_text) -> tuple[set[str], set[str]]:
    """-> (the paths the diff adds, modifies, deletes or renames; the top-level def or class names of its changed hunks, names of at least DOCS_IMPACT_SYMBOL_MIN characters)."""
    paths: set = set()
    syms: set = set()
    cur, hunk = "", False
    for ln in diff_text.splitlines():
        if ln.startswith("diff --git "):
            cur, hunk = "", False
        elif ln.startswith("@@"):
            hunk = True
            syms.add(_header_def(ln))
        elif hunk:
            syms.add(_def_name(ln[1:]) if ln[:1] in ("+", "-") else None)
        else:
            cur = cite_check._header_file(ln, cur, paths)
            if cur:
                paths.add(cur)
    return paths, {s for s in syms if s and len(s) >= DOCS_IMPACT_SYMBOL_MIN and s not in DOCS_IMPACT_GENERIC_SYMBOLS}


def doc_files(worktree, docs_dir) -> list[str]:
    """-> the repo-relative *.md paths under docs_dir of the worktree, sorted."""
    root = os.path.join(worktree, docs_dir)
    out: list[str] = []
    for folder, _dirs, files in os.walk(root):
        out.extend(Path(os.path.relpath(os.path.join(folder, f), worktree)).as_posix() for f in files if f.endswith(cite_check.CITE_MD_EXTENSIONS))
    return sorted(out)


def gate_on(setting, stage) -> bool:
    """-> whether the docs impact check runs for `stage`. A bool is the switch of every stage; a dict {"default": bool, <stage>: bool} gives a stage its own switch and the others the `default` one (DOCS_IMPACT_DEFAULT when absent)."""
    if not isinstance(setting, dict):
        return bool(setting)
    return bool(setting.get(stage, setting.get(DOCS_IMPACT_STAGE_DEFAULT_KEY, DOCS_IMPACT_DEFAULT)))


def _doc_text(worktree, doc):
    """-> the text of the doc in the worktree, None when it is not a file."""
    full = os.path.join(worktree, doc)
    if not os.path.isfile(full):
        return None
    with open(full, encoding="utf-8", errors="replace") as f:
        return f.read()


def _exempt(doc, globs) -> bool:
    """-> True when doc matches one of the exempt globs of the project settings."""
    return any(fnmatch.fnmatchcase(doc, g) for g in globs)


def _cited_paths(text) -> set[str]:
    """-> the paths of the `path:line` cites of the doc text."""
    return {c[0] for line in text.splitlines() for c in cite_check.cites(line)}


def check(worktree, diff_text, docs_files, cfg) -> list[str]:
    """-> [message] for each changed path or top-level def a doc under docs_files cites while the diff leaves that doc unchanged. A path is cited when the doc names it word-bounded or as a path:line cite; a def when the doc has it in backticks. Docs named by cfg's docs_impact_exempt globs are not read."""
    paths, syms = _changes(diff_text)
    if not paths and not syms:
        return []
    exempt = cfg.get(DOCS_IMPACT_EXEMPT_KEY, [])
    msgs: set[str] = set()
    for doc in docs_files:
        if doc in paths or _exempt(doc, exempt):
            continue
        text = _doc_text(worktree, doc)
        if text is None:
            continue
        cited = _cited_paths(text)
        for p in paths:
            if p in cited or re.search(DOCS_IMPACT_PATH_RE.format(path=re.escape(p)), text):
                msgs.add(f"{doc} cites {p} which the diff changes, but {doc} is not changed")
        for sym in syms:
            if re.search(DOCS_IMPACT_TICK_RE.format(sym=re.escape(sym)), text):
                msgs.add(f"{doc} cites `{sym}` which the diff changes, but {doc} is not changed")
    return sorted(msgs)
