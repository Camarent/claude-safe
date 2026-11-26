"""safeshell: a read-only shell for the local explorer. check() accepts a command line only when every simple command is on the allow list;
run() executes it in a throwaway copy of a repo with an empty environment. No network tool, no write, no absolute path outside the repo."""

import re
import shlex
import subprocess

from pipeline.configuration import tool_setting
from pipeline.shellwords import short_flag

ALLOW = {"head", "tail", "grep", "egrep", "sed", "ls", "cat", "wc", "sort", "uniq", "find", "echo", "cut", "jq", "git", "lp"}
GIT_OK = ("ls-files", "log", "grep", "show", "blame")
LP_OK = ("find", "nav")
OPS = {"|", "|&", "||", "&&", "&", ";", ";;"}
PATTERN_CMDS = ("grep", "egrep", "sed")  # the first non-option word is a pattern or script, not a path
MAX_OUT = 6000


def _simple_commands(cmd):
    """[[tokens]] split at pipes and list operators; ValueError when shlex cannot read CMD."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars="|&;<>")
    lex.whitespace_split = True
    out: list[list[str]] = []
    cur: list[str] = []
    for t in lex:
        if t in OPS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _words(tokens):
    """TOKENS without the harmless redirects `2>/dev/null`, `>/dev/null`, `2>&1`; None when any other redirect is present."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t and set(t) <= set("<>&"):
            if t == ">" and i + 1 < len(tokens) and tokens[i + 1] == "/dev/null" or t == ">&" and i + 1 < len(tokens) and tokens[i + 1] in ("1", "2"):
                i += 2
            else:
                return None
            if out and out[-1] in ("1", "2"):
                out.pop()
            continue
        out.append(t)
        i += 1
    return out


SED_WRITE_RE = re.compile(r"(^|[;{}\s!$\d,/])[rRwWeE]\b|/[gpIiMm0-9]*[wWeE]\b")


def sed_script_writes(script):
    """True when a sed script can read another file or write/execute: a `r`, `w` or `e` command (after `;`, `{`, `}`, blank, `!`, an address or at the start), or such a flag after `s/a/b/`."""
    return bool(SED_WRITE_RE.search(script))


def _pre_reason(cmd):
    """Reason to refuse CMD from its raw text, before parsing; None when it passes."""
    if "`" in cmd or "$(" in cmd or "${" in cmd or "<<" in cmd:
        return "no command substitution or heredoc"
    if "{" in cmd or "~" in cmd:
        return "no brace or ~ expansion"
    if "\n" in cmd.strip():
        return "one line only"
    return None


def _is_grep_like(name, args):
    return name in ("grep", "egrep") or name == "git" and args[:1] == ["grep"]


def _pattern_index(name, args):
    """Index of the pattern word in ARGS (not a path), or None."""
    if name not in PATTERN_CMDS and not _is_grep_like(name, args):
        return None
    return next((i for i, a in enumerate(args) if not a.startswith("-") and not (name == "git" and i == 0)), None)


def _path_reason(args, pattern):
    for i, a in enumerate(args):
        if i == pattern:
            continue
        vals = [a, a.split("=", 1)[1]] if "=" in a else [a]  # --opt=/path too
        if any(v.startswith("/") or ".." in v.split("/") for v in vals):
            return "absolute and parent paths are not allowed; use paths relative to the repo"
    return None


def _git_reason(name, args):
    if name != "git":
        return None
    if not args or args[0] not in GIT_OK or any(x.startswith(("--output", "--ext-diff", "-c")) for x in args):
        return "git: allowed subcommands: " + ", ".join(GIT_OK)
    return None


def _grep_reason(name, args):
    if _is_grep_like(name, args) and any(x == "-f" or x.startswith("--file") or short_flag(x, "f") for x in args):
        return "grep: no -f or --file (reads a pattern file)"
    if name == "git" and args[0] == "grep" and any(x.startswith("--open-files") or short_flag(x, "O") for x in args):
        return "git grep: no -O (runs a pager command)"
    return None


def _lp_reason(name, args):
    if name == "lp" and (not args or args[0] not in LP_OK):
        return "lp: allowed subcommands: find, nav"
    return None


def _sort_uniq_reason(name, args):
    if name == "sort" and any(x.startswith("--output") or short_flag(x, "o") for x in args):
        return "sort: no -o or --output (writes a file)"
    if name == "uniq" and sum(not x.startswith("-") for x in args) > 1:
        return "uniq: one input only (a second word is an output file)"
    return None


def _sed_scripts(args, pattern):
    explicit = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-e", "--expression")]
    return explicit or (args[pattern : pattern + 1] if pattern is not None else [])


def _sed_reason(name, args, pattern):
    if name != "sed":
        return None
    bad_flag = any(a in ("-i", "-f", "-s") or a.startswith(("-i", "--in-place", "--file")) for a in args)
    if bad_flag or any(sed_script_writes(s) for s in _sed_scripts(args, pattern)):
        return "sed: read-only scripts only (no -i, -f, r, w, e)"
    return None


def _find_reason(name, args):
    if name == "find" and any(a in ("-exec", "-execdir", "-ok", "-okdir", "-delete") or a.startswith(("-fprint", "-fls")) for a in args):
        return "find: no -exec, -delete or -fprint"
    return None


def _command_reason(name, args):
    """Reason to refuse one simple command NAME ARGS; None when allowed."""
    if name not in ALLOW:
        return f"`{name}` is not allowed; allowed: {', '.join(sorted(ALLOW))}"
    pattern = _pattern_index(name, args)
    return (
        _path_reason(args, pattern)
        or _git_reason(name, args)
        or _grep_reason(name, args)
        or _lp_reason(name, args)
        or _sort_uniq_reason(name, args)
        or _sed_reason(name, args, pattern)
        or _find_reason(name, args)
    )


def check(cmd):
    """None when CMD is allowed, else a one-line reason."""
    why = _pre_reason(cmd)
    if why:
        return why
    try:
        simple = _simple_commands(cmd)
    except ValueError as e:
        return f"cannot parse: {e}"
    if not simple:
        return "empty command"
    for toks in simple:
        words = _words(toks)
        if words is None:
            return "no redirect except to /dev/null"
        if not words:
            continue
        why = _command_reason(words[0], words[1:])
        if why:
            return why
    return None


def run(cmd, cwd, timeout=None):
    """Output of CMD run in CWD with an empty environment; the check() reason when refused. TIMEOUT None: the SAFESHELL_TIMEOUT_S tool setting."""
    why = check(cmd)
    if why:
        return "error: " + why
    if timeout is None:
        timeout = tool_setting("SAFESHELL_TIMEOUT_S")
    try:
        p = subprocess.run(
            ["bash", "-c", cmd],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": cwd},
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"error: timeout {timeout} s"
    return ((p.stdout + p.stderr).strip() or "(no result)")[:MAX_OUT]
