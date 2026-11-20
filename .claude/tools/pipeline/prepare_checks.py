"""prepare-repo checks that only REPORT: malformed AUDIT.md audit-tools lines, a missing `worktrees` setting, gate tools missing from PATH. Nothing is written or installed here."""

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile

from .configuration import (
    AUDIT_TOOLS_MIN_FIELDS,
    AUDIT_TOOLS_TAG,
    PATH_HINT,
    PIPELINE_JSON,
    PYTHON_MODULE_PROBES,
    SHELL_BUILTINS,
    SHELL_KEYWORDS,
    TOOL_PROBE_TIMEOUT_S,
    WORKTREES_KEY,
    all_gates_of,
    project,
)
from .util import read_text

BLOCK_RE = re.compile(r"^```" + re.escape(AUDIT_TOOLS_TAG) + r"\s*\n(.*?)^```", re.M | re.S)
ASSIGN_RE = re.compile(r"^\w+=")


def malformed_audit_lines(repo):
    """-> report lines for audit-tools lines with too few fields (never rewritten). No block, no report."""
    cfg = project(repo)
    path = os.path.join(repo, str(cfg.get("audit_file")))
    text = read_text(path)
    m = BLOCK_RE.search(text)
    if not m:
        return []
    first = text[: m.start(1)].count("\n") + 1
    out = []
    for i, line in enumerate(m.group(1).splitlines()):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        n = len(line.split("|"))
        if n < AUDIT_TOOLS_MIN_FIELDS:
            out.append(
                f"{os.path.basename(path)}:{first + i}: audit-tools line has {n} fields, needs {AUDIT_TOOLS_MIN_FIELDS} (name | cwd | command | parser): {line.strip()}"
            )
    return out


def worktrees_missing(repo):
    """True when pipeline.json (or its absence) leaves the `worktrees` key unset."""
    path = os.path.join(repo, PIPELINE_JSON)
    if not os.path.exists(path):
        return True
    try:
        data = json.loads(read_text(path))
    except ValueError:
        return False  # broken file: not ours to judge
    return isinstance(data, dict) and WORKTREES_KEY not in data


OPERATOR_TOKENS = frozenset({"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"})  # shell tokens that end a command (prepare_checks._first_words)


def _tokens(cmd):
    """-> the shell tokens of CMD, quote-aware, with the operators (`&&`, `;`, `|`, parens, redirects) as tokens of their own; the plain split when the quotes do not balance."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""  # shlex treats `#` inside a word as a comment, a shell only at the start of a word
        return list(lex)
    except ValueError:
        return cmd.split()


REDIRECT_RE = re.compile(r"\d*[<>]+&?\d*")  # a redirect token as shlex splits it: `>`, `>>`, `<`, `>&`, `2>`


def _segments(cmd):
    """-> the token lists of the commands of a gate command: split at the operator tokens, redirects dropped with their target and the file descriptor number in front (`2>&1`, `2>/dev/null`)."""
    toks = _tokens(cmd)
    segs: list[list[str]] = []
    cur: list[str] = []
    skip = False
    for i, t in enumerate(toks):
        if t in OPERATOR_TOKENS:
            segs.append(cur)
            cur, skip = [], False
        elif skip:
            skip = False
        elif REDIRECT_RE.fullmatch(t):
            skip = True  # the word after a redirect is its target (`>&` is followed by a descriptor number)
        elif t.isdigit() and i + 1 < len(toks) and REDIRECT_RE.fullmatch(toks[i + 1]):
            continue  # the descriptor number of `2>&1`
        else:
            cur.append(t)
    return [*segs, cur]


def _command_of(seg):
    """-> (program word, tokens) of one command's tokens, minus leading env assignments and shell keywords; None for a builtin or nothing."""
    toks = list(seg)
    while toks and (ASSIGN_RE.match(toks[0]) or toks[0] in SHELL_KEYWORDS):
        toks = [] if toks[0] in ("for", "select", "case") else toks[1:]  # after these comes a variable or a pattern, not a program
    return (toks[0], toks) if toks and toks[0] not in SHELL_BUILTINS else None


def _first_words(cmd):
    """-> [(command word, tokens)] of each command of a gate command (`&&`, `||`, `;`, `|`, subshells; quotes respected), minus env assignments, shell builtins and keywords. A command with a substitution (`$(...)`, backticks) yields only its own first word."""
    substituted = "$(" in cmd or "`" in cmd
    segs = [_tokens(cmd)[:1]] if substituted else _segments(cmd)
    return [c for c in map(_command_of, segs) if c]


def _probe(python, module):
    """True when PYTHON can import MODULE. The probe runs in an empty temp dir (a `pytest.py` in the project must not be found through the cwd) with the normal environment (PYTHONPATH and the user site count, as they do for the gate); stdin is closed."""
    try:
        with tempfile.TemporaryDirectory() as empty:
            r = subprocess.run(
                [python, "-c", f"import {module}"], check=False, capture_output=True, stdin=subprocess.DEVNULL, cwd=empty, timeout=TOOL_PROBE_TIMEOUT_S
            )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def toolchain_problems(repo):
    """-> one line per gate tool missing from PATH, or per python module (pytest) the gate's interpreter cannot import."""
    out, seen = [], set()
    for g in all_gates_of(project(repo)):
        for word, toks in _first_words(g["cmd"]):
            exe = shutil.which(os.path.join(repo, word) if os.sep in word and not os.path.isabs(word) else word)
            if not exe:
                if word not in seen:
                    seen.add(word)
                    out.append(f"gate `{g['name']}`: `{word}` is not on PATH: {PATH_HINT}")
                continue
            for mod in PYTHON_MODULE_PROBES:
                if "-m" in toks and toks[toks.index("-m") + 1 : toks.index("-m") + 2] == [mod] and (word, mod) not in seen:
                    seen.add((word, mod))
                    if not _probe(exe, mod):
                        out.append(
                            f"gate `{g['name']}`: `{word}` cannot import `{mod}`: install it in that interpreter's environment (or point the gate at the venv's python)"
                        )
    return out
