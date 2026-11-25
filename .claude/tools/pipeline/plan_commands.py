"""Command-path checks for plan.py check, without running anything: the executable and the path-like arguments of a `done_when`, `Test:` or pipeline.json gate command must exist (in the repo or a nested repo of the task) or be made by the plan. A bare argument that names exactly one directory of the repo gets a `did you mean` warning. Conservative: a segment with quotes, pipes, redirects, `$`, env prefixes or globs is skipped silently."""

import os
import re
import subprocess

from . import gitx
from .configuration import (
    CMD_BUILD_DIRS,
    CMD_OUTPUT_OPTS,
    CMD_REF_MARKERS,
    CMD_REF_PREFIX,
    CMD_REF_TOOLS,
    CMD_SUGGEST_DEPTH,
    CMD_WRAPPER_OPTS,
    CMD_WRAPPERS,
    WORK_DIR,
)
from .plan_rules import _same
from .shellwords import split_commands

WARN_PREFIX = "cmd: "  # a warning line: never fails a check (plan_check.errors_of)
UNUSUAL_CHARS = frozenset("'\"`$|<>(){}\\")  # a segment holding one of these is too unusual to judge: skipped
DYNAMIC_CHARS = "*?[]{}$~="  # glob, variable, home or assignment: an argument holding one is not a fixed path
PATH_ARG_RE = re.compile(r"^[\w.@+-]*(?:/[\w.@+-]*)+$")  # relative path shape (no URL, no colon, no leading slash)
BARE_WORD_RE = re.compile(r"^[A-Za-z][\w-]*$")


def _dynamic(word):
    return any(c in word for c in DYNAMIC_CHARS)


def _segments(cmd):
    """-> the words of each `&&`/`||`/`;`/newline/`&` segment of CMD; None for a segment too unusual to judge."""
    out = []
    for seg in split_commands(cmd, pipe=False):
        words = seg.split()
        out.append(None if not words or any(c in UNUSUAL_CHARS for c in seg) else words)
    return out


def _strip_wrappers(words):
    """-> WORDS without the leading wrapper words (`cs-qrun --`, `python3 -I`, `node`, `bash`) and their option words."""
    i = 0
    while i < len(words) and os.path.basename(words[i]) in CMD_WRAPPERS:
        i += 1
        while i < len(words) and words[i] in CMD_WRAPPER_OPTS:
            i += 1
    return words[i:]


def _exists(path, dirs, produced):
    """True when PATH exists under one of DIRS, the plan makes it (PRODUCED), a build output dir holds it, or it is under the stage work dir (a task writes it at run time)."""
    norm = os.path.normpath(path)
    if norm.split(os.sep)[0] in (*CMD_BUILD_DIRS, WORK_DIR) or any(_same(norm, p) for p in produced):
        return True
    return any(os.path.exists(os.path.join(d, path)) for d in dirs)


def repo_dirs(repo):
    """-> the directories (CMD_SUGGEST_DEPTH parts deep at most) of the git-tracked files of REPO; set() when REPO is no git work tree."""
    try:
        r = gitx.run(repo, "ls-files", "-z")
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if r.returncode:
        return set()
    out: set[str] = set()
    for f in r.stdout.split("\0"):
        parts = f.split("/")[:-1]
        out.update("/".join(parts[:n]) for n in range(1, min(len(parts), CMD_SUGGEST_DEPTH) + 1))
    return out


def _suggestion(word, repo):
    """-> the one repo directory whose path ends with `/word`, else None (no match or several)."""
    hits = [d for d in repo_dirs(repo) if d.endswith("/" + word)]
    return hits[0] if len(hits) == 1 else None


def _ref_like(word):
    """-> True when WORD is a git revision or full ref name (`HEAD~1`, `master..dev`, `refs/tags/v1`), not a file path."""
    return word.startswith(CMD_REF_PREFIX) or any(m in word for m in CMD_REF_MARKERS)


def _missing_path_finding(label, a, script):
    """-> the finding for a path-like argument A that does not exist: a problem when SCRIPT (a local file runs), else a warning."""
    msg = f"{label}: argument {a} does not exist: fix the path or let a task create it"
    return msg if script else WARN_PREFIX + msg


def _bare_word_finding(label, a, repo):
    """-> a `did you mean` warning for bare word A when exactly one repo directory ends with it, else None."""
    hit = _suggestion(a, repo)
    return f"{WARN_PREFIX}{label}: argument {a} is not a file here: did you mean {hit}?" if hit else None


def _arg_findings(label, args, repo, dirs, produced, local, script):
    """-> findings for the arguments of one command. A path-like argument must exist: a problem when SCRIPT (the command runs a local file), else only a warning. A bare word that names one repo directory gets a warning, only for a LOCAL executable (`./tool`) and never right after an option (`-p sample-lib` names a package, not a path)."""
    out = []
    for i, a in enumerate(args):
        if _skip_arg(args, i):
            continue
        if "/" in a and PATH_ARG_RE.match(a):
            found = None if _exists(a, dirs, produced) else _missing_path_finding(label, a, script)
        elif local and _bare_candidate(args, i) and not _exists(a, dirs, produced):
            found = _bare_word_finding(label, a, repo)
        else:
            found = None
        if found:
            out.append(found)
    return out


def _skip_arg(args, i):
    """-> True when argument I is not judged: an option, a dynamic or ref-like word, or the output file of an output option."""
    a = args[i]
    return a.startswith("-") or _dynamic(a) or _ref_like(a) or bool(i and args[i - 1] in CMD_OUTPUT_OPTS)


def _bare_candidate(args, i):
    """-> True when argument I is a bare word that may name a repo directory: not an option value (`-p sample-lib`)."""
    return not (i and args[i - 1].startswith("-")) and bool(BARE_WORD_RE.match(args[i]))


def _segment_findings(label, words, repo, dirs, produced):
    """-> findings for one segment (already split): wrappers stripped, then the executable and the arguments."""
    stripped = _strip_wrappers(words)
    wrapped = len(stripped) < len(words)
    words = stripped
    if not words or words[0].startswith("-") or _dynamic(words[0]):
        return []  # nothing to run, a module flag (`python3 -m x`) or an env prefix
    exe = words[0]
    out = []
    if "/" in exe and not _exists(exe, dirs, produced):
        out.append(f"{label}: runs {exe}, which does not exist: fix the path or let a task create it")
    if os.path.basename(exe) in CMD_REF_TOOLS:
        return out  # version control and forge CLIs: the arguments are refs or ids, not files
    script = "/" in exe or (wrapped and _exists(exe, dirs, produced))  # a local file runs (directly or under a wrapper)
    return out + _arg_findings(label, words[1:], repo, dirs, produced, local="/" in exe, script=script)


def command_findings(label, cmd, repo, produced=(), nested=()):
    """-> problems (and `cmd:` warnings) for one shell command CMD of LABEL, run in REPO. PRODUCED: paths the plan makes; NESTED: nested repo dirs the task edits (their files count as found). A leading `cd x` moves the base to x."""
    dirs = [repo, *nested]
    out = []
    for words in _segments(cmd):
        if words is None:
            continue
        if words[0] == "cd":
            if len(words) == 2 and not _dynamic(words[1]):
                dirs = [os.path.join(repo, words[1]), *nested]
            continue
        out += _segment_findings(label, words, repo, dirs, produced)
    return out
