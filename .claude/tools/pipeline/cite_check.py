"""Cite check: every `path:line` cite in the doc and comment lines a task adds must point at a real line, and a symbol named before the cite must sit near it.

Pure: the diff text and the worktree files come in as arguments; no git call happens here (`gitx.added_lines` only parses the diff text)."""

import os
import re

from . import gitx
from .configuration import (
    CITE_COMMENT_MARKS,
    CITE_DEF_RE,
    CITE_FENCE_RE,
    CITE_FIXED_WORDS,
    CITE_MD_EXTENSIONS,
    CITE_MSG_PATH_RE,
    CITE_QUOTED_RE,
    CITE_RE,
    CITE_REPORT_NAME,
    CITE_SKIP_DIRS,
    CITE_SPLIT_MIN_REMOVED,
    CITE_SYMBOL_BEFORE_RE,
    CITE_URL_RE,
    CITE_WINDOW_LINES,
    CITE_WORD_RE,
)


def _prose(rows):
    """-> the (line number, text) rows that are not inside a fenced ``` block; rows are whole-file lines in order."""
    fenced = False
    out = []
    for ln, text in rows:
        if CITE_FENCE_RE.match(text):
            fenced = not fenced
        elif not fenced:
            out.append((ln, text))
    return out


def added_lines(diff_text) -> list[tuple[str, int, str]]:
    """-> [(file, line number, text)] of the lines a `git diff` text adds: every added line of `*.md` files (check tells their fenced blocks apart from the whole file), and the comment tail of code lines."""
    out: list = []
    for file, rows in gitx.added_lines(diff_text).items():
        ext = os.path.splitext(file)[1]
        if ext in CITE_MD_EXTENSIONS:
            out.extend((file, ln, text) for ln, text in rows)
        elif ext in CITE_COMMENT_MARKS:
            tail = re.compile(r"(?:^|\s)" + re.escape(CITE_COMMENT_MARKS[ext]) + r"\s?(.*)$")
            for ln, text in rows:
                if m := tail.search(text):
                    out.append((file, ln, m[1]))
    return out


def _header_file(ln, cur, out):
    """-> the file a diff header line names (cur when it names none); a deleted or renamed file is added to out."""
    if ln.startswith("rename from "):
        out.add(gitx.unquote_path(ln[len("rename from ") :]))
    elif ln.startswith("rename to "):
        cur = gitx.unquote_path(ln[len("rename to ") :])
        out.add(cur)
    elif ln.startswith("+++ /dev/null") and cur:
        out.add(cur)  # a deleted file: its `--- a/` side named it
    elif ln.startswith(("--- ", "+++ ")):
        raw = gitx.unquote_path(ln[4:].rstrip("\t"))
        if raw.startswith(("a/", "b/")):
            cur = raw[2:]
    return cur


def split_files(diff_text) -> set[str]:
    """-> the files a `git diff` text splits: deleted or renamed files, and files whose diff removes at least CITE_SPLIT_MIN_REMOVED lines (their lines moved, so a cite by line into them is not read)."""
    out: set = set()
    removed: dict = {}
    cur, hunk = "", False
    for ln in diff_text.splitlines():
        if ln.startswith("diff --git "):
            cur, hunk = "", False
        elif ln.startswith("@@"):
            hunk = True
        elif hunk:
            if ln.startswith("-"):
                removed[cur] = removed.get(cur, 0) + 1
        else:
            cur = _header_file(ln, cur, out)
    return out | {f for f, n in removed.items() if n >= CITE_SPLIT_MIN_REMOVED}


def bare_names(msgs) -> bool:
    """-> True when a cite message names its file by a bare file name (a path with no directory)."""
    paths = (CITE_MSG_PATH_RE.search(msg) for msg in msgs)
    return any("/" not in m[1] for m in paths if m)


def cites(text) -> list[tuple[str, int | None, int | None, str]]:
    """-> [(path, line, end line or None, text before the cite)] for each `path:line` or `path:start-end` in one line of text; URLs are not cites."""
    text = CITE_URL_RE.sub(" ", text)
    return [(m[1], int(m[2]), int(m[3]) if m[3] else None, text[: m.start()]) for m in CITE_RE.finditer(text)]


def _first_line(lines, sym):
    """-> the 1-based line of the first definition of sym, else of its first occurrence; None when sym is not in lines."""
    pats = [re.compile(CITE_DEF_RE.format(sym=re.escape(sym))), re.compile(CITE_WORD_RE.format(sym=re.escape(sym)))]
    for pat in pats:
        for i, line in enumerate(lines, 1):
            if pat.search(line):
                return i
    return None


def _quoted_fixed(text):
    """-> the (path, line, end) cites of one line that sit in backticks or quotes when the line also has a CITE_FIXED_WORDS word: the report quotes a bad cite as already fixed."""
    if not re.search(r"\b(?:" + "|".join(map(re.escape, CITE_FIXED_WORDS)) + r")\b", text, re.IGNORECASE):
        return set()
    out: set = set()
    for quoted in CITE_QUOTED_RE.findall(text):
        out.update((path, line, end) for path, line, end, _ in cites(quoted))
    return out


def _inside(worktree, path):
    """-> the real path of path under worktree, or None when it leaves the worktree (a cite may use `..`)."""
    root = os.path.realpath(worktree)
    full = os.path.realpath(os.path.join(root, path))
    return full if os.path.commonpath([root, full]) == root else None


def _bare_hits(worktree, path):
    """-> every worktree-relative file named path (outside CITE_SKIP_DIRS) when path has no directory; [] when it has one."""
    if os.sep in path or "/" in path:
        return []
    found = []
    for root, dirs, files in os.walk(worktree):
        dirs[:] = [d for d in dirs if d not in CITE_SKIP_DIRS]
        if path in files:
            found.append(os.path.relpath(os.path.join(root, path), worktree))
    return found


def _resolve_bare(worktree, path):
    """-> the worktree-relative path of the one file named path when path has no directory and exactly one such file exists outside CITE_SKIP_DIRS; else path unchanged."""
    if os.sep in path or "/" in path:
        return path
    found = _bare_hits(worktree, path)
    return found[0] if len(found) == 1 else path


def _lines(full):
    """-> the lines of the file at full, None when it is not a file."""
    if not os.path.isfile(full):
        return None
    with open(full, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def _prose_numbers(worktree, src):
    """-> line numbers of the md file src outside its fenced blocks, read from the whole file; None when src cannot be read."""
    full = _inside(worktree, src)
    lines = _lines(full) if full else None
    if lines is None:
        return None
    return {ln for ln, _ in _prose(enumerate(lines, 1))}


def _symbol_msg(where, path, lines, line, before):
    """-> the message when a symbol named before the cite is not within CITE_WINDOW_LINES of line in lines; None when it holds or no symbol precedes the cite."""
    sym = CITE_SYMBOL_BEFORE_RE.search(before)
    if not sym:
        return None
    name = sym[1]
    near = lines[max(0, line - 1 - CITE_WINDOW_LINES) : line + CITE_WINDOW_LINES]
    word = re.compile(CITE_WORD_RE.format(sym=re.escape(name)))
    if any(word.search(near_line) for near_line in near):
        return None
    real = _first_line(lines, name)
    if real is None:
        return f"{where}: symbol not found in {path}"
    return f"{where} but {name} is at {real}"


def _cite_msg(worktree, where, path, line, end, before, relaxed=False):
    """-> the message for one cite that does not hold, None when it holds. relaxed: the file is split, so only its existence and the outside-worktree test count."""
    full = _inside(worktree, path)
    if full is None:
        return f"{where} but {path} is outside the worktree"
    lines = _lines(full)
    if lines is None:
        resolved = _resolve_bare(worktree, path)
        full = _inside(worktree, resolved)
        lines = _lines(full) if full else None
    if lines is None:
        return f"{where} but {path} is missing"
    if relaxed:
        return None
    if (end or line) > len(lines):
        return f"{where} but {path} has {len(lines)} lines"
    return _symbol_msg(where, path, lines, line, before)


def check(worktree, added, report_text, split=()) -> list[str]:
    """-> [message] for the cites in the added lines and in the dev report text that do not hold: path outside the worktree, missing file, line past the end, or a symbol named before the cite that is not within CITE_WINDOW_LINES of it. A report cite into a split file (split_files) skips the line-past-end and symbol tests only."""
    rows = [(CITE_REPORT_NAME, ln, text) for ln, text in _prose(enumerate(report_text.splitlines(), 1))]
    msgs = []
    for src, ln, text in _kept_lines(worktree, added) + rows:
        msgs += _line_msgs(worktree, src, ln, text, split)
    return msgs


def _kept_lines(worktree, added) -> list:
    """The added lines (src, ln, text) that are not in the prose of a markdown source: markdown counts only its prose lines."""
    prose = {}
    for src in {s for s, _, _ in added if os.path.splitext(s)[1] in CITE_MD_EXTENSIONS}:
        prose[src] = _prose_numbers(worktree, src)
    return [(s, ln, t) for s, ln, t in added if prose.get(s) is None or ln in prose[s]]


def _line_msgs(worktree, src, ln, text, split) -> list[str]:
    """The messages for the cites in one line that do not hold (quoted fixed cites are skipped)."""
    skip = _quoted_fixed(text)
    msgs = []
    for path, line, end, before in cites(text):
        if (path, line, end) in skip:
            continue
        where = f"{src}:{ln} cites {path}:{line}" + (f"-{end}" if end else "")
        relaxed = src == CITE_REPORT_NAME and path in split
        msg = _cite_msg(worktree, where, path, line, end, before, relaxed)
        if msg:
            msgs.append(msg)
    return msgs
