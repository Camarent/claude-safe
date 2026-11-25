#!/usr/bin/env python3
"""cmd_guard: PreToolUse hook for Bash. Denies four always-wrong command shapes with a clear message (rule 4 is rewritten when that is safe).

1. A pipe, `||`, `;` or newline AFTER `qrun.py` (the pipe returns the other command's status and hides a red or empty run; a second
   command runs outside qrun). `&&` is fine when the next segment is itself a qrun call (a chain of qruns keeps the exit status honest) or a read-only command (configuration QRUN_CHAIN_READ_ONLY). A `cd dir && python3 -I qrun.py -- cmd` prefix is fine. `sh -c '...'` / `bash -c "..."` bodies are checked too.
2. An `env`, `VAR=value` or `export` before a test command (qrun.py, node --test, pytest, cargo test, npm test, ctest, make test ...; list = configuration TEST_RUNNERS or pipeline.json `test_runners`), also behind `timeout N`:
   headless agents are denied anyway and qrun injects the test env itself.

3. A `python -m http.server` without `--bind 127.0.0.1` (configuration SERVE_HOST): a UI check may serve on the loopback address only, never 0.0.0.0.

4. `cd <dir>` followed (same command, through `&&` or `;`) by `git ...` or `snip run -- git ...`: Claude Code's built-in rule denies it anyway
   (a cd before a version-control command can pick up untrusted hooks). The hook REWRITES the command (`cd D && git x | head` -> `git -C D x | head`,
   `-C D` right after `git`, also behind `snip run --`) when that is equivalent: the cd target is a plain word (no `~`, `$`, quote, glob), the cd is
   joined by `&&` or `;`, and no later segment up to the next cd depends on the cwd (see `cwd_dependent`: relative path or bare word argument, no argument,
   redirect, substitution). The rewrite is printed as `updatedInput` (no permissionDecision: normal permission rules still judge the new text) and
   only when `verdict()` of the new text is clean. Otherwise the call is denied with the message `git -C <dir> <args>`.
   A `cd` into the hook's own cwd (the job worktree) is not denied: it is dropped by the rewrite (`cd . && git x` -> `git x`).

Quoted text and heredoc bodies are data, not commands, except the body of `sh -c` / `bash -c`.
Known limit (A11): `$(...)` and backtick subshells and `(...)` groups are not split, so a qrun call inside them is not checked; a hook crash exits 1 and the call is allowed (fail open).
Setup: settings.json runs `python3 -I /root/.claude/tools/cmd_guard.py`; if that path is missing python exits 2 and blocks every Bash
call, so restore the file (or fix the hook path) rather than the guard.

Reads the hook JSON on stdin; prints a deny decision, a rewrite (updatedInput), or nothing to allow. Always exit 0.
"""

import contextlib
import json
import os
import re
import shlex
import sys
from pathlib import Path

IMPORT_FAILURE = ""  # set when the pipeline package does not import: verdict() then denies every command (fail closed)
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import denial_log
    from pipeline.configuration import (
        CLEANUP_WT_DIR,
        QRUN_CHAIN_READ_ONLY,
        QRUN_PATTERN,
        SED_PRINT_CMD,
        SERVE_HOST,
        SERVE_SCRIPT,
        SERVE_SUBCOMMANDS,
        SERVE_WRAPPER,
        TOOLS_DIR,
    )
    from pipeline.serve_rule import start_refusal
    from pipeline.shellwords import split_commands, strip_heredocs
except ImportError as err:  # fail closed: the guard cannot split commands, so it denies them all with a clear reason
    IMPORT_FAILURE = f"cmd_guard: cannot import the pipeline package ({err}); every Bash command is denied until it imports again. Restore the tools directory."
    QRUN_PATTERN = r"(?:python3?\s+(?:-\w+\s+)*)?[\"']?\S*qrun\.py[\"']?|(?:\S*/)?cs-qrun"  # placeholder, never used for a verdict
    SERVE_HOST = "127.0.0.1"
    QRUN_CHAIN_READ_ONLY = ()  # placeholder: verdict() denies everything first
    TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
    FALLBACK_SPLIT = re.compile(r"&&|\|\||;|\||\n")

    def split_commands(cmd, pipe=True, amp=True, newline=True):
        """Minimal splitter used only when pipeline.shellwords cannot be imported (verdict() denies first)."""
        return FALLBACK_SPLIT.split(cmd)

    def strip_heredocs(cmd, keep_opener=True):
        """Fallback: heredoc bodies stay in the text (verdict() denies first)."""
        return cmd


ENV_PROJECT_DIR = "PIPELINE_PROJECT_DIR"  # project dir holding pipeline.json (default: the hook's cwd)


def load_runners():
    """Test-run head patterns: configuration TEST_RUNNERS, or `test_runners` of the project's pipeline.json. Fails open to qrun only."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from pipeline.configuration import PROJECT_FILE, test_runners
    except ImportError:
        return (QRUN_PATTERN,)
    cfg = {}
    with contextlib.suppress(OSError, ValueError):
        cfg = json.loads(Path(os.environ.get(ENV_PROJECT_DIR) or os.getcwd(), PROJECT_FILE).read_text(encoding="utf-8"))
    return test_runners(cfg if isinstance(cfg, dict) else {})


def compile_runners(runners):
    """-> (one head pattern for the runners that compile, the entries that do not)."""
    good, bad = [], []
    for r in runners:
        try:
            re.compile(r)
            good.append(f"(?:{r})(?=\\s|$)")
        except re.error:
            bad.append(r)
    return re.compile("|".join(good) if good else r"(?!)"), bad


TEST_HEAD, BAD_RUNNERS = compile_runners(load_runners())
if BAD_RUNNERS:
    print(f"cmd_guard: ignoring invalid test runner patterns: {BAD_RUNNERS}", file=sys.stderr)
QRUN_HEAD = re.compile(rf"(?:{QRUN_PATTERN})(?=\s|$)")
WRAPPERS = {"env", "nohup", "time", "exec", "command", "nice", "builtin"}
ASSIGN_WORDS = {"export", "declare", "typeset", "local", "readonly"}
VAR_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
TIMEOUT_VALUE_FLAGS = {"-k", "--kill-after", "-s", "--signal"}  # timeout flags whose value is the next word
SHELL_C = re.compile(r"\b(?:ba|z|da)?sh\s+(?:-\w+\s+)*-\w*c\s+('[^']*'|\"(?:\\.|[^\"\\])*\")", re.S)  # the quoted body, escapes kept


def shell_c_bodies(cmd):
    """-> the body of each `sh -c` / `bash -c` argument, quotes and escapes removed (shlex reads the quoted token)."""
    return [shlex.split(quoted)[0] for quoted in SHELL_C.findall(strip_heredocs(cmd))]


def strip_quotes(cmd):
    """Heredoc bodies go; inside quotes `;`, `|`, `&` and newline become `_` so they do not count as operators. Quote marks stay."""
    cmd = strip_heredocs(cmd).replace("\\\n", " ")
    return re.sub(r"'[^']*'|\"[^\"]*\"", lambda m: m.group(0)[0] + re.sub(r"[;&|\n]", "_", m.group(0)[1:-1]) + m.group(0)[-1], cmd)


def parse(seg):
    """-> (env_prefix, command head, assigns) for one command segment."""
    words = seg.lstrip("({ ").split()
    env = assigns = False
    while words:
        w = words[0]
        if w in WRAPPERS or w.startswith("-"):
            env = env or w == "env"
            words.pop(0)
        elif w == "timeout":
            words = words[1:]
            while words and words[0].startswith("-"):
                takes_value = words.pop(0) in TIMEOUT_VALUE_FLAGS
                if takes_value and words:
                    words.pop(0)  # the value of `-k 5` or `-s TERM` is not the duration
            words = words[1:]  # the duration
        elif VAR_WORD.match(w):
            env = True
            words.pop(0)
        else:
            break
    if not words:
        return env, "", env
    if words[0] in ASSIGN_WORDS and any("=" in w for w in words[1:]):
        assigns = True
    return env, " ".join(words), assigns


SERVE_HEAD = re.compile(r"python3?\s+(?:-\w+\s+)*-m\s+http\.server\b")
SERVE_BIND = re.compile(r"(?:--bind|-b)\s+(\S+)")


def serve_why(seg):
    """-> deny reason for a `python -m http.server` that is not bound to the loopback address only, else ''."""
    if not SERVE_HEAD.search(seg):
        return ""
    binds = SERVE_BIND.findall(seg)  # python takes the last --bind, so every one must be the loopback address
    if binds and all(b == SERVE_HOST for b in binds):
        return ""
    return f"A static serve must bind {SERVE_HOST} only: `python3 -m http.server --bind {SERVE_HOST} <port>`. No default bind, no 0.0.0.0. Stop the server you started when the check ends."


OPERATORS = re.compile(r"(\|\||&&|;|\||(?<![<>])&(?!>)|\n)")
QRUN_MSG = (
    "One qrun per Bash call. No pipe, `||`, `;`, `&` or newline after qrun.py: a pipe hides the test exit status (a red or empty run looks green) "
    "and a second command runs outside qrun. To see the result, run the command bare and read the exit code. "
    f"After qrun, `&&` is allowed only before another qrun call or a read-only command ({', '.join(QRUN_CHAIN_READ_ONLY)}). "
    f"Run `python3 -I {TOOLS_DIR}/qrun.py -- <command>` alone (a leading `cd dir &&` is fine); qrun already trims the output."
)


def _read_only(seg):
    """-> True when the command head of `seg` is in configuration QRUN_CHAIN_READ_ONLY."""
    words = parse(seg)[1].split()
    return bool(words) and os.path.basename(words[0]) in QRUN_CHAIN_READ_ONLY


SUBSTITUTION = re.compile(r"\$\(|`|<\(")  # command substitution or process substitution runs a second command
REDIRECT = re.compile(r"[<>]")  # any redirect: a read-only tail may not write or read a file


def _simple_tail(seg):
    """-> True when a `&&` tail is one read-only command with no redirect and no substitution."""
    return _read_only(seg) and not REDIRECT.search(seg) and not SUBSTITUTION.search(seg)


def _chain_ok(segs, ops, i):
    """-> True when every segment after qrun segment i is joined by `&&` and is another qrun call or one simple read-only command.
    A blank segment is ignored only after a newline: after `&` (background) or `&&` it is a deny."""
    for op, seg in zip(ops[i:], segs[i + 1 :], strict=True):  # equal length: one op before each later segment
        if not seg.strip() and op == "\n":
            continue
        if op != "&&":
            return False
        if QRUN_HEAD.match(parse(seg)[1]):
            if SUBSTITUTION.search(seg):
                return False
        elif not _simple_tail(seg):
            return False
    return True


def qrun_why(text):
    """-> deny reason when a qrun call is followed by anything but `&&` before another qrun call or a read-only command, else ''."""
    parts = OPERATORS.split(text)
    segs, ops = parts[0::2], parts[1::2]
    for i, seg in enumerate(segs):
        if QRUN_HEAD.match(parse(seg)[1]) and not _chain_ok(segs, ops, i):
            cd = next((s.split()[1] for s in segs[:i] if s.split()[:1] == ["cd"] and len(s.split()) > 1), "")
            if cd:
                return (
                    QRUN_MSG
                    + f" Your command starts with `cd {cd}`: run the qrun call as its own Bash call from `{cd}` (the Test: working dir), and the other parts as separate calls."
                )
            return QRUN_MSG
    return ""


def git_words(seg):
    """-> the words of a `git ...` command (also behind `snip run --`), else []."""
    words = [w.strip("\"'") for w in parse(seg)[1].split()]
    if len(words) > 1 and os.path.basename(words[0]) == "snip" and words[1] == "run":
        words = words[2:]
        if words[:1] == ["--"]:
            words = words[1:]
    return words if words and words[0] == "git" else []


def in_cwd(directory):
    """-> True when DIRECTORY is the hook's cwd (the job worktree): a plain `cd` there changes nothing."""
    return not UNSAFE_DIR.search(directory) and os.path.realpath(directory) == os.path.realpath(os.getcwd())


def git_suggestion(seg, directory):
    """-> the git command of SEG as `git -C DIRECTORY ...`, quotes kept (the real segment text); `<args>` when it has none."""
    new = insert_git_c(seg.strip(), directory) or f"git -C {directory}"
    return new if len(git_words(seg)) > 1 else f"{new} <args>"


def cd_git_why(segs):
    """-> deny reason for a `cd <dir>` segment followed by a git command without its own `-C` in the same command line, else ''."""
    cd_dir = ""
    for seg in segs:
        words = seg.split()
        if words[:1] == ["cd"]:
            cd_dir = words[1] if len(words) > 1 else "<dir>"
            if in_cwd(cd_dir):
                cd_dir = ""
        elif cd_dir and git_words(seg) and git_words(seg)[1:2] != ["-C"]:
            in_wt = CLEANUP_WT_DIR in Path(os.getcwd()).parts  # the hook's cwd is a job worktree
            return (
                f"No `cd {cd_dir}` before git: Claude Code denies a cd before a version-control command (untrusted hooks). "
                f"Run `{git_suggestion(seg, cd_dir)}` instead"
                + (", or plain `git <args>` (your cwd is already the job worktree)" if in_wt else "")
                + ". `-c safe.directory` is not needed."
            )
    return ""


UNSAFE_DIR = re.compile(r"[~$`\"'\\*?\[\]{}()<>|&;!#=%]")  # a cd target the rewrite cannot carry over to `git -C` unchanged
ANY_ARGS_OK = {"echo", "true", "false", ":", "printf"}  # never read the cwd by themselves
STDIN_FILTERS = {"head", "tail", "wc", "sort", "uniq", "cat", "tac", "rev"}  # after a pipe with no file argument they read stdin
BENIGN_REDIRECT = re.compile(r"\s\d?>&\d\b|\s\d?>\s*/dev/null\b")
REDIRECT_SUBST = re.compile(r"[<>]|\$\(|`|<\(")


def cwd_dependent(seg, piped=False):
    """-> True when SEG may behave differently if the cwd is not the cd target (relative path or bare word argument, no argument, redirect, substitution). Unsure means True."""
    if REDIRECT_SUBST.search(BENIGN_REDIRECT.sub(" ", seg)):
        return True
    words = parse(seg)[1].split()
    if not words:
        return True
    head, args = os.path.basename(words[0]), [w for w in words[1:] if not w.startswith("-")]
    if head in ANY_ARGS_OK:
        return False
    if piped and head in STDIN_FILTERS:
        return any(not a.startswith("/") for a in args)
    return not args or any(not a.startswith("/") for a in args)


def plain_cd(words, op):
    """-> True for `cd DIR` (one plain word) joined to the next segment by `&&` or `;`, not backgrounded."""
    return len(words) == 2 and not UNSAFE_DIR.search(words[1]) and not words[1].startswith("-") and op in ("&&", ";")


def insert_git_c(seg, directory):
    """-> SEG with `-C DIRECTORY` right after its `git` word, or '' when there is not exactly one such word."""
    new, n = re.subn(r"(^|\s)git(?=\s|$)", lambda m: f"{m.group(1)}git -C {directory}", seg, count=1)
    return new if n == 1 else ""


def git_seg_with_dir(seg_text, orig, directory):
    """-> the git segment ORIG with `-C DIRECTORY`, or '' when its redirect or substitution may depend on the cwd."""
    if REDIRECT_SUBST.search(BENIGN_REDIRECT.sub(" ", seg_text)):
        return ""
    return insert_git_c(orig, directory)


def after_cd_segment(seg_text, orig, directory, piped):
    """-> ORIG of a segment that ran after `cd DIRECTORY`: a git command gets `-C DIRECTORY`, another one stays; '' when it may depend on the cwd."""
    if git_words(seg_text):
        return git_seg_with_dir(seg_text, orig, directory)
    return "" if cwd_dependent(seg_text, piped) else orig


def _cd_target(words):
    """-> the directory a plain `cd` WORDS moves into, '' for a cd into the cwd (dropped: the cwd does not change)."""
    return "" if in_cwd(words[1]) else words[1]


def _segment_rows(cmd):
    """-> (segment text, original text, operator, piped) per command segment of CMD, or None when CMD has a shape the rewrite skips."""
    text = strip_quotes(cmd)
    pieces = OPERATORS.split(text)
    # heredoc / continuation strip changes offsets; groups are not split; a background command is not rewritten
    if len(text) != len(cmd) or re.search(r"[(){}]|<<", text) or "&" in pieces[1::2]:
        return None
    rows, pos = [], 0
    for i in range(0, len(pieces), 2):
        seg_text = pieces[i]
        orig = cmd[pos : pos + len(seg_text)]
        op = pieces[i + 1] if i + 1 < len(pieces) else ""
        pos += len(seg_text) + len(op)
        rows.append((seg_text, orig, op, i > 0 and pieces[i - 1] == "|"))
    return rows


class _CdScope:
    """The `cd DIR` in effect for the rest of a command line; GROUP is set when a cwd-dependent command needs the cd kept in a subshell."""

    def __init__(self):
        self.dir, self.seen, self.group = "", False, False
        self.at, self.entry = 0, ("", "")  # the cd's index in the output pairs and its (text, operator) pair

    def enter(self, words, orig, op, at):
        self.dir = _cd_target(words)  # a cd into the cwd is dropped: the cwd does not change
        self.seen, self.at, self.entry = True, at, (orig, op)

    def keep_cd(self, seg_text, piped, rewrote):
        """-> False when SEG needs the cd but a git command already ran (no rewrite); else True. A cwd-dependent SEG sets GROUP."""
        if not self.dir or git_words(seg_text) or not cwd_dependent(seg_text, piped):
            return True
        self.group = True  # the cd stays with this command: a subshell, not a dropped cd
        return not rewrote

    def git_seg(self, out, seg_text, orig, rewrote):
        """-> the git segment ORIG with `-C DIR` (closing the pending subshell first), or '' when it cannot be rewritten."""
        if self.group:
            joiner = out[-1][1]  # the operator between the last cwd command and git
            if rewrote or joiner == "|":
                return ""
            out[self.at :] = [(" (" + _group_body([self.entry, *out[self.at :]]) + ") ", joiner)]
            self.group = False
        return git_seg_with_dir(seg_text, orig, self.dir)


def rewrite_cd_git(cmd):
    """-> CMD with every `cd DIR` dropped and each git command after it changed to `git -C DIR ...`, or '' when that is not provably equivalent.
    A cwd-dependent command (`ls`) before the first git keeps its cd in a subshell: `(cd DIR && ls) && git -C DIR ...`."""
    rows = _segment_rows(cmd)
    if rows is None:
        return ""
    out: list[tuple[str, str]] = []  # (segment text, operator) pairs
    scope, rewrote = _CdScope(), False
    for seg_text, orig, op, piped in rows:
        words = seg_text.split()
        if words[:1] == ["cd"]:
            if not plain_cd(words, op) or scope.group:
                return ""
            scope.enter(words, orig, op, len(out))
            continue
        new = scope.git_seg(out, seg_text, orig, rewrote) if scope.dir and git_words(seg_text) else orig
        if not new or not scope.keep_cd(seg_text, piped, rewrote):
            return ""
        rewrote = rewrote or (scope.seen and bool(git_words(seg_text)))
        out.append((new, op))
    if scope.group:
        return ""  # no git command after the cd: nothing to rewrite
    return "".join(o + p for o, p in out).strip() if rewrote else ""


def _group_body(pairs):
    """-> the commands of PAIRS (text, operator) joined as one line, for a `( ... )` subshell."""
    return " ".join(f"{o.strip()} {p}".strip() for o, p in pairs[:-1]) + " " + pairs[-1][0].strip()


def _quiet_flag(w):
    """-> True when word W is a quiet option (`-n`, `--quiet`, `--silent`) or a short cluster holding n (`-ne`): such a sed call is checked here."""
    return w in ("-n", "--quiet", "--silent") or (w.startswith("-") and not w.startswith("--") and "n" in w)


def _sed_scripts(args):
    """-> the script texts of `sed -n ARGS`: each `-e SCRIPT`, else the first operand; None when ARGS holds an option other than -e or the quiet flag."""
    scripts, operands, it = [], [], iter(args)
    for w in it:
        if w == "-e":
            scripts.append(next(it, ""))
        elif w in ("-n", "--quiet", "--silent"):
            continue
        elif w.startswith("-"):
            return None
        else:
            operands.append(w)
    if not scripts:
        if not operands:
            return None
        scripts = [operands.pop(0)]
    return scripts


def sed_why(segs):
    """-> deny reason for a `sed -n` call whose script is not one print command (`N`, `N,M`, `$`, `/re/`, `N~M` addresses with `p`; several print scripts go in several -e) or that has an option other than -e (-i, -f, -s, ...): no w/e/r/s commands. `sed -n` is the only form the default allowlist opens (SED_PRINT_ALLOW), so only it is checked here; any other sed form is left to the project's own allow rules. Else ''."""
    deny = "`sed -n` is allowed only to print lines (`sed -n 'A,Bp' <file>`, addresses N, N,M, $, /re/, N~M with p). Read a slice with the Read tool (offset, limit) and change files with the Edit tool."
    for seg in segs:
        try:  # shell words, quotes removed: a quoted `;` or space stays inside one script word
            words = shlex.split(parse(seg)[1])
        except ValueError:  # unbalanced quotes: a sed call like that is denied, any other segment is left alone
            if "sed" in seg:
                return deny
            continue
        if len(words) > 2 and os.path.basename(words[0]) == "snip" and words[1] == "run":
            words = words[3:] if words[2] == "--" else words[2:]
        if not words or os.path.basename(words[0]) != "sed" or not any(_quiet_flag(w) for w in words[1:]):
            continue
        scripts = _sed_scripts(words[1:])
        if scripts is None or not all(re.fullmatch(SED_PRINT_CMD, s) for s in scripts):  # one print command per script; several go in several -e
            return deny
    return ""


def servectl_why(segs):
    """-> deny reason for a servectl call whose subcommand is not start, stop or list, or whose `start --` command is not allowed (serve_rule.py, the same rule the tool enforces), else ''."""
    for seg in segs:
        words = [w.strip("\"'") for w in parse(seg)[1].split()]
        at = next((i for i, w in enumerate(words) if os.path.basename(w) in (SERVE_WRAPPER, SERVE_SCRIPT)), None)
        if at is None:
            continue
        sub = words[at + 1] if at + 1 < len(words) else ""
        if sub not in SERVE_SUBCOMMANDS:
            return (
                f"servectl takes only {', '.join(SERVE_SUBCOMMANDS)}: `{SERVE_WRAPPER} start --ttl S -- <command>`, `{SERVE_WRAPPER} stop`, `{SERVE_WRAPPER} list`. "
                "Never kill or pkill the server; `stop` ends it."
            )
        if sub == "start" and "--" in words[at:]:
            inner = words[words.index("--", at) + 1 :]
            why = start_refusal(inner, os.getcwd())  # same rule as servectl.py start
            if why:
                return f"{why} Denied by cmd_guard before it reaches servectl."
    return ""


def segments_why(segs):
    """-> deny reason for a serve call or a test command with an env prefix in SEGS, else ''."""
    seen_assign = False
    for seg in segs:
        env, head, assigns = parse(seg)
        why = serve_why(seg)
        if why:
            return why
        seen_assign = seen_assign or assigns
        if TEST_HEAD.match(head) and env and QRUN_HEAD.match(head):
            return (
                f"qrun applies the test env itself: run `{head}` alone. To set a variable for the test, set it inside the test script, "
                "or put `env` in a script file and run that file."
            )
        if TEST_HEAD.match(head) and (env or seen_assign):
            return (
                "Set the variable inside the test script, or put `env` in a script file and run that file. "
                f"Then run the test as `python3 -I {TOOLS_DIR}/qrun.py -- <command>`."
            )
    return ""


def verdict_rule(cmd):
    """-> (rule, deny reason) or ('', '') when allowed; the rule names the check that denied (denial log)."""
    if IMPORT_FAILURE:
        return "import", IMPORT_FAILURE
    for body in shell_c_bodies(cmd):
        rule, why = verdict_rule(body)
        if why:
            return rule, why
    text = strip_quotes(cmd)
    segs = split_commands(text)
    for rule, why in (
        ("qrun", lambda: qrun_why(text)),
        ("cd_git", lambda: cd_git_why(segs)),
        ("servectl", lambda: servectl_why(segs)),
        ("sed", lambda: sed_why(segs)),
    ):
        reason = why()
        if reason:
            return rule, reason
    reason = segments_why(segs)
    return ("segments", reason) if reason else ("", "")


def verdict(cmd):
    """-> deny reason or ''."""
    return verdict_rule(cmd)[1]


def git_cd_reason(why):
    """-> True when WHY is the cd-before-git denial (the only one a rewrite can fix)."""
    return why.startswith("No `cd ")


def main():
    try:
        tool_input = json.load(sys.stdin).get("tool_input", {})
        cmd = tool_input.get("command", "")
    except (ValueError, AttributeError):
        return 0
    cmd = cmd if isinstance(cmd, str) else ""
    rule, why = verdict_rule(cmd)
    if not why or git_cd_reason(why):  # a cd into the cwd is allowed by verdict but still rewritten: Claude Code denies any cd before git
        new = rewrite_cd_git(cmd)
        if new and not verdict(new):
            ti = dict(tool_input)
            ti["command"] = new
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": ti}}))
            return 0
    if why:
        if not IMPORT_FAILURE:  # the log module imports the pipeline package too: no log when that failed
            denial_log.append(rule, cmd, os.getcwd())
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
