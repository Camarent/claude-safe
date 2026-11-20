#!/usr/bin/env python3
"""review_guard: PreToolUse hook for Edit, Write, NotebookEdit and Bash. Lets the reviewer run mutation checks in its scratch copy only.

Active when the env var PIPELINE_REVIEW_DIR (pipeline.configuration.ENV_REVIEW_DIR) holds the job worktree path, and for any session whose hook input names the `reviewer` agent
(`agent_type`): without the variable that session has no worktree, so it may write only report files and run no mutating command (fail closed). Every other session is untouched.
Then
1. Edit/Write/NotebookEdit are allowed only for real paths (symlinks and `..` resolved) under the job worktree, or a report file `.work/<stage>/reports/<file>`;
2. a Bash command is denied when it visibly writes outside the job worktree (`mktemp` may create files under the scratch dir env TMPDIR,
   `mkdir` and `cat` are not checked, `snip run <cmd>` is judged as <cmd>): a redirect, `tee`, `sed -i`, `rm`/`mv`/`cp`/`touch`/`ln`/`chmod`/`dd of=`,
   `git checkout -- <paths>` and `git restore` (undo a mutation) only with the cwd or `-C` inside the job worktree, the read-only forms of `branch`, `tag`, `remote`, `stash`, `worktree`, `config`
   anywhere, and every other git command that changes the work tree, index or shared repo (commit, add, reset, clean, push, update-ref ...) never; `xargs` (unless it runs only ls/cat/head/tail/wc/grep/file/stat/test), `sudo`, `find -delete/-exec` and `--output=` are denied.
Known limits: no full shell parser. `$(...)`, variables in a path (denied when they are a write target), `python -c`/`node -e` writes and
scripts that write files are NOT detected; the brief and the agent file carry the rest of the rule.
A malformed hook input (with the env var set) exits 2 with the reason on stderr (fail closed).

Reads the hook JSON on stdin; prints a deny decision, or nothing to allow. Exit 0, except 2 for malformed input.
"""

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cmd_guard
from pipeline.configuration import ENV_REVIEW_DIR, ENV_SCRATCH_DIR, HOST_AGENT_PREFIX, SCRATCH_ENV, SNIP_WRAPPER, WORK_DIR
from pipeline.shellwords import short_flag, split_commands

PATH_KEYS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
DEST_ONLY = {"cp", "install", "rsync", "ln"}
WRITERS = {"rm", "mv", "cp", "touch", "rmdir", "ln", "chmod", "chown", "truncate", "install", "rsync", "patch"}  # mkdir is allowed anywhere (owner decision)
GIT_DENIED = {
    "commit",
    "add",
    "reset",
    "checkout",
    "switch",
    "stash",
    "clean",
    "rebase",
    "merge",
    "push",
    "restore",
    "branch",
    "tag",
    "worktree",
    "config",
    "update-ref",
    "cherry-pick",
    "revert",
    "am",
    "apply",
    "rm",
    "mv",
    "gc",
    "prune",
    "remote",
    "fetch",
    "pull",
    "symbolic-ref",
}
TOOLS_DENIED = {"commit.py", "wt.py"}  # the driver commits and resets, never the reviewer
NO_WORKTREE = os.sep + "review-guard-no-worktree"  # root of a reviewer session that has none: nothing but report files is writable
REVIEWER_AGENT = "reviewer"


def _is_reviewer(agent_type):
    """The container agent is `reviewer`; host_agents.py installs it as `pl-reviewer`; other `*-reviewer` agents (pl-ux-reviewer) are not this one."""
    return isinstance(agent_type, str) and agent_type in (REVIEWER_AGENT, HOST_AGENT_PREFIX + REVIEWER_AGENT)


SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "(", "time"}
DENIED_HEADS = {"sudo", "doas"}
XARGS_READ_ONLY = {"ls", "cat", "head", "tail", "wc", "grep", "file", "stat", "test"}  # the only commands `xargs` may run
XARGS_VALUE_OPTS = {
    "-I",
    "-n",
    "-L",
    "-P",
    "-d",
    "-E",
    "-s",
    "-a",
    "-i",
    "-l",
    "--max-args",
    "--max-lines",
    "--max-procs",
    "--delimiter",
    "--arg-file",
    "--replace",
}
XARGS_BAD_ARGS = {"-C", "-o", "--output", "--compile", "-exec", "-delete"}  # flags that write or run something (file -C, sort -o)
FIND_DENIED = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf"}
LIST_WHEN_BARE = {"branch", "tag", "remote"}  # bare, these only list
UNDO_GIT = {"checkout", "restore"}  # allowed inside the job worktree: the way to restore after a mutation check
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env"}
READONLY_GIT = {  # sub -> (words one of which must lead the args, other allowed options, words that allow positional arguments)
    "branch": (set(), {"--show-current", "-v", "-vv", "-a", "-r", "--all", "--remotes"}, {"-l", "--list", "--contains", "--merged", "--no-merged"}),
    "tag": (set(), {"-n"}, {"-l", "--list", "--contains", "--merged", "--no-merged"}),
    "remote": ({"-v", "--verbose", "show", "get-url"}, set(), {"show", "get-url"}),
    "stash": ({"list", "show"}, {"-p", "--patch", "--stat"}, {"list", "show"}),
    "worktree": ({"list"}, {"--porcelain", "-v", "--verbose", "-z"}, set()),
    "config": (
        {"--get", "--get-all", "--list", "-l", "--get-regexp"},
        {"--local", "--global", "--system", "--show-origin", "--null", "-z", "--name-only"},
        {"--get", "--get-all", "--get-regexp"},
    ),
}
UNKNOWN_CWD = os.sep + "review-guard-unknown-cwd"  # a `cd` whose target the guard cannot resolve: relative writes land outside the job worktree
GIT_REDIRECT_OPTS = {"--work-tree", "--git-dir", "--namespace"}
GIT_REDIRECT_CONFIG = re.compile(r"^(?:-c)?\s*core\.(?:worktree|gitdir)\b", re.I)  # `-c core.worktree=X` moves git like --work-tree
GIT_CMD_CONFIG = re.compile(
    r"^\s*(?:core\.(?:fsmonitor|sshcommand|pager|editor|hookspath|askpass|worktree|gitdir)|credential(?:\..*)?\.helper|pager\..*|diff\.external|gpg\..*program)$",
    re.I,
)  # `-c` keys that run a command
CONFIG_SCOPE_OPTS = {"--local", "--global", "--system", "--show-origin", "--null", "-z", "--name-only"}  # may precede the lead word of `git config`
GIT_EXPORT = re.compile(r"\bexport\s+GIT_\w+=")
GIT_ENV = re.compile(r"^GIT_\w+=")
TARGET_DIR_CMDS = {"cp", "install", "ln"}  # `-t DIR` names the destination
REDIRECT = re.compile(r"^(?:\d*|&)>>?\|?(.*)$")
DEVNULL = "/dev/null"


def under(path, root):
    return path == root or path.startswith(root + os.sep)


def is_report(real):
    """The reviewer's own report: <...>/.work/<stage>/reports/<file>; a phase stage is deeper (.work/<feature>/phases/<NN-x>/reports/<file>)."""
    parts = real.split(os.sep)
    return len(parts) >= 4 and parts[-2] == "reports" and WORK_DIR in parts[:-3]


def resolve(tok, cwd):
    return os.path.realpath(os.path.join(cwd, os.path.expanduser(tok)))


def malformed(data):
    if not isinstance(data, dict) or not isinstance(data.get("tool_input"), dict):
        return "review_guard: hook input must be an object with an object tool_input; the call cannot be checked."
    if not isinstance(data.get("tool_name"), str):
        return "review_guard: `tool_name` must be a string; the call cannot be checked."
    if data.get("cwd") is not None and not isinstance(data.get("cwd"), str):
        return "review_guard: `cwd` must be a string; the call cannot be checked."
    return ""


def outside(why_path, root):
    return f"Reviewer sessions may write only inside the job worktree {root} or the stage scratch dir (env {ENV_SCRATCH_DIR}); {why_path} is outside both. Edit the job worktree only, never the main checkout or another folder."


def file_verdict(tool, tool_input, root):
    key = PATH_KEYS[tool]
    p = tool_input.get(key)
    if not isinstance(p, str) or not p:
        return f"review_guard: `{key}` must be a non-empty string; the call cannot be checked."
    real = resolve(p, root)
    return "" if under(real, root) or is_report(real) or in_scratch_write(real, root) else outside(p, root)


def target_ok(tok, cwd, root):
    """A write target is fine when it is /dev/null or resolves under the job worktree or the stage scratch dir (no variables or substitutions)."""
    if tok == DEVNULL:
        return True
    if re.search(r"[$`*?]", tok):
        return False
    real = resolve(tok, cwd)
    return under(real, root) or in_scratch_write(real, root)


def report_target(tok, cwd):
    """True when TOK (a redirect target) is the reviewer's own report file (is_report), as the Write tool allows."""
    return not re.search(r"[$`*?]", tok) and is_report(resolve(tok, cwd))


def in_scratch_write(real, root):
    """True when REAL (already resolved) is under the stage scratch dir (env ENV_SCRATCH_DIR); an unset or root dir, or one holding the job worktree, allows nothing."""
    s = os.environ.get(ENV_SCRATCH_DIR, "")
    s = os.path.realpath(s) if s else ""
    return s not in ("", os.sep) and not under(root, s) and under(real, s)


def moves_git(args, i):
    """True when the option at ARGS[i] points git elsewhere (--work-tree/--git-dir/--namespace, `-c core.worktree=X`)."""
    a = args[i]
    if a.split("=")[0] in GIT_REDIRECT_OPTS or a.startswith("--config-env") or GIT_REDIRECT_CONFIG.match(a):
        return True
    return a == "-c" and i + 1 < len(args) and bool(GIT_REDIRECT_CONFIG.match(args[i + 1]))


def after_option(args, i):
    """Index of the word after the leading git option at ARGS[i] (an option in GIT_VALUE_OPTS also takes the next word as its value)."""
    return i + (2 if args[i] in GIT_VALUE_OPTS else 1)


def runs_command(args):
    """True when a leading `-c key=val` / `--config-env` option of git ARGS makes git run a command or move (fsmonitor, sshCommand, pager, `!` alias, ...)."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a.startswith("--config-env"):
            return True
        if a == "-c" or (a.startswith("-c") and not a.startswith("--")):
            val = args[i + 1] if a == "-c" and i + 1 < len(args) else a[2:]
            key, _, v = val.partition("=")
            if GIT_CMD_CONFIG.match(key) or (key.lower().startswith("alias.") and v.lstrip().startswith("!")):
                return True
        i = after_option(args, i)
    return False


def git_split(args, cwd):
    """-> (subcommand, its args, cwd after any -C, True when --work-tree/--git-dir/--namespace points git elsewhere)."""
    i, moved = 0, False
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            cwd = resolve(args[i + 1], cwd)
        moved = moved or moves_git(args, i)
        i = after_option(args, i)
    sub, rest = (args[i], args[i + 1 :]) if i < len(args) else ("", [])
    return sub, rest, cwd, moved


def config_args(rest):
    """-> REST of `git config` without the scope options that may precede the lead word; one bare key (a plain read) becomes `--get KEY`."""
    rest = [a for a in rest if a not in CONFIG_SCOPE_OPTS] or rest[:1]
    return ["--get", *rest] if len(rest) == 1 and not rest[0].startswith("-") else rest


def readonly_git(sub, rest):
    """True when `git SUB REST` is exactly a read-only form (list/show); any other option or word denies."""
    if sub not in READONLY_GIT:
        return False
    if not rest:
        return sub in LIST_WHEN_BARE
    lead, opts, pos = READONLY_GIT[sub]
    if sub == "config":
        rest = config_args(rest)
    if (rest[0] not in lead) if lead else not rest[0].startswith("-"):
        return False
    if any(a.startswith("-") and a not in lead | opts | pos for a in rest):
        return False
    words = [a for a in rest[1:] if not a.startswith("-")] if lead else [a for a in rest if not a.startswith("-")]
    return not words or bool(pos & set(rest))


def git_allowed(sub, rest, cwd, root, moved):
    """True for an undo inside ROOT (git not pointed elsewhere) or a read-only form."""
    if sub in UNDO_GIT and not moved and under(cwd, root) and (sub == "restore" or "--" in rest):
        return True
    return readonly_git(sub, rest)


def git_verdict(args, cwd, root, env_moved=False):
    """args: tokens after `git`. -> deny reason or ''."""
    sub, rest, cwd, moved = git_split(args, cwd)
    if runs_command(args):
        return "Reviewer sessions may not use `git --config-env` or `-c` with a key that runs a command."
    if any(a.startswith("--output") for a in rest):
        return "Reviewer sessions may not use `--output`: it writes a file."
    if git_allowed(sub, rest, cwd, root, moved or env_moved):
        return ""
    if sub in GIT_DENIED:
        return f"Reviewer sessions may not run `git {sub}` here: undo a mutation with `git checkout -- <paths>` inside the job worktree; the driver resets it after you."
    return ""


def redirect_verdict(words, cwd, root):
    """-> deny reason when a redirect in WORDS (quotes kept: a quoted `>` is an argument) writes outside ROOT, else ''."""
    for i, w in enumerate(words):
        m = REDIRECT.match(w)
        if m and w != "2>&1" and not re.match(r"^\d*>&\d+$", w):
            tgt = (m.group(1) or (words[i + 1] if i + 1 < len(words) else "")).strip("'\"")
            if not (target_ok(tgt, cwd, root) or report_target(tgt, cwd)):
                return outside(f"the redirect target `{tgt}`", root)
    return ""


def target_dir(args):
    """-> the value of `-t DIR`, `-tDIR`, `-rt DIR` or `--target-directory[=]DIR` in ARGS, else ''."""
    for i, a in enumerate(args):
        if a.startswith("--target-directory="):
            return a.split("=", 1)[1]
        if a == "--target-directory" or (a.startswith("-") and not a.startswith("--") and a.endswith("t")):
            return args[i + 1] if i + 1 < len(args) else ""
        if a.startswith("-t") and not a.startswith("--"):
            return a[2:]
    return ""


def cd_target(args, cwd):
    """-> cwd after `cd ARGS`; UNKNOWN_CWD for a bare cd, `cd -`, options, `~`, variables or substitutions."""
    if len(args) != 1 or args[0].startswith(("-", "~")) or re.search(r"[$`]", args[0]):
        return UNKNOWN_CWD
    return resolve(args[0], cwd)


def scratch_dir():
    """-> the real path of the agent scratch dir (env SCRATCH_ENV), or '' when unset or the filesystem root (too broad to trust)."""
    d = os.environ.get(SCRATCH_ENV, "")
    real = os.path.realpath(d) if d else ""
    return "" if real in ("", os.sep) else real


def in_scratch(tok, cwd):
    """True when TOK resolves (symlinks and `..` resolved) under the scratch dir; no variables, globs or substitutions."""
    s = scratch_dir()
    return bool(s) and not re.search(r"[$`*?]", tok) and under(resolve(tok, cwd), s)


def mktemp_verdict(args, cwd, root):
    """-> deny reason when mktemp would create a file outside ROOT or the scratch dir, else ''. A bare template lands in TMPDIR."""
    targets = []
    for i, a in enumerate(args):
        if a in ("-p", "--tmpdir") and i + 1 < len(args):
            targets.append(args[i + 1])
        elif a.startswith("--tmpdir="):
            targets.append(a.split("=", 1)[1])
        elif not a.startswith("-") and "/" in a:
            targets.append(a)
    bad = [t for t in targets if not (target_ok(t, cwd, root) or in_scratch(t, cwd))]
    return outside(f"`mktemp {bad[0]}`", root) if bad else ""


def write_files(cmd, args):
    files = [a for a in args if not a.startswith("-")]
    if cmd == "sed" and not any(a in ("-e", "-f") or a.startswith(("--expression", "--file")) for a in args):
        return files[1:]  # the first word is the script, not a path
    if cmd in TARGET_DIR_CMDS and (tgt := target_dir(args)):
        return [tgt]
    if cmd in DEST_ONLY:
        return files[-1:]  # a source is only read
    return files


def write_ok(tok, cwd, root):
    """True when TOK is inside the job worktree."""
    return target_ok(tok, cwd, root)


def snip_verdict(args, cwd, root, exported):
    """-> (deny reason or '', cwd) for `snip run <cmd>`: judged as <cmd>, so it is allowed only when <cmd> is; None for other forms."""
    if args[:1] != ["run"]:
        return None
    return chain_verdict(shlex.join(args[1:]), cwd, root, exported) if len(args) > 1 else ("", cwd)


def xargs_denied(args):
    """True unless the command xargs runs is one of XARGS_READ_ONLY with no writing flag (no sh -c, rm, cp, sed -i ...)."""
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "--":
        i += 2 if args[i] in XARGS_VALUE_OPTS else 1
    i += args[i : i + 1] == ["--"]
    rest = args[i:]
    return not rest or os.path.basename(rest[0]) not in XARGS_READ_ONLY or any(a.split("=")[0] in XARGS_BAD_ARGS or short_flag(a, "o") for a in rest[1:])


def head_denied(cmd, args):
    """True for a head that is never allowed: sudo, doas, xargs of a writer, find -delete/-exec and the like."""
    return cmd in DENIED_HEADS or (cmd == "find" and bool(FIND_DENIED & set(args))) or (cmd == "xargs" and xargs_denied(args))


def writer_verdict(cmd, args, cwd, root):
    """-> deny reason when CMD writes a file outside ROOT (tee, WRITERS, sed -i, dd of=), else ''."""
    files = write_files(cmd, args)
    if cmd == "tee" or cmd in WRITERS or (cmd == "sed" and any(a.startswith("--in-place") or short_flag(a, "i") for a in args)):
        bad = [f for f in files if not write_ok(f, cwd, root)]
        if bad:
            return outside(f"`{cmd} {bad[0]}`", root)
    if cmd == "dd" and any(a.startswith("of=") and not target_ok(a[3:], cwd, root) for a in args):
        return outside("the `dd of=` target", root)
    return ""


def seg_words(seg):
    """Words of SEG with quotes kept, or None when unparsable."""
    try:
        lex = shlex.shlex(seg, posix=False)
        lex.whitespace_split = True
        lex.commenters = ""
        words = list(lex)
        shlex.split(seg, posix=True)
    except ValueError:
        return None
    return words


def denied_form(cmd):
    return f"Reviewer sessions may not run `{cmd}` with this form: it can write or delete outside the job worktree."


def head_words(seg):
    """Command words of SEG without leading shell keywords; [] when none or unparsable."""
    _env, head, _assigns = cmd_guard.parse(seg)
    try:
        h = shlex.split(head)
    except ValueError:
        return []
    while h and h[0] in SHELL_KEYWORDS:  # `do rm x`, `then rm x`: the command is what follows
        h = h[1:]
    return h


def segment_verdict(seg, cwd, root, exported=False):
    """-> (deny reason or '', new cwd)."""
    words = seg_words(seg)
    if words is None:
        return "review_guard: unparsable command; cannot check where it writes.", cwd
    why = redirect_verdict(words, cwd, root)
    if why:
        return why, cwd
    h = head_words(seg)
    if not h:
        return "", cwd
    cmd, args = os.path.basename(h[0]), h[1:]
    if head_denied(cmd, args):
        return denied_form(cmd), cwd
    if cmd == "cd":
        return "", cd_target(args, cwd)
    if cmd == SNIP_WRAPPER and (judged := snip_verdict(args, cwd, root, exported)) is not None:
        return judged
    if any(os.path.basename(x) in TOOLS_DENIED for x in h[:3]):
        return "Reviewer sessions may not run commit.py or wt.py: the driver commits and resets.", cwd
    if cmd == "git":
        return git_verdict(args, cwd, root, exported or any(GIT_ENV.match(w) for w in words)), cwd
    if cmd == "mktemp":
        return mktemp_verdict(args, cwd, root), cwd
    return writer_verdict(cmd, args, cwd, root), cwd


def chain_verdict(cmd, cwd, root, exported=False):
    """-> (deny reason or '', cwd after CMD). Each `sh -c` body is judged on its own; each segment moves the cwd for the next."""
    for body in cmd_guard.SHELL_C.findall(cmd_guard.strip_heredocs(cmd)):
        why = bash_verdict(body[1], cwd, root)
        if why:
            return why, cwd
    exported = exported or bool(GIT_EXPORT.search(cmd))  # `export GIT_DIR=..;` on an earlier segment moves every later git
    for seg in split_commands(cmd_guard.strip_quotes(cmd)):
        why, cwd = segment_verdict(seg, cwd, root, exported)
        if why:
            return why, cwd
    return "", cwd


def bash_verdict(cmd, cwd, root):
    return chain_verdict(cmd, cwd, root)[0]


def verdict(data, root):
    """-> deny reason or ''. data: the hook JSON (already checked by malformed)."""
    tool, ti = data["tool_name"], data["tool_input"]
    root = os.path.realpath(root)
    if tool in PATH_KEYS:
        return file_verdict(tool, ti, root)
    if tool == "Bash":
        c = ti.get("command")
        if not isinstance(c, str):
            return "review_guard: `command` must be a string; the call cannot be checked."
        return bash_verdict(c, data.get("cwd") or os.getcwd(), root)
    return ""


def main():
    root = os.environ.get(ENV_REVIEW_DIR)
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = None
    if not root:
        if not (isinstance(data, dict) and _is_reviewer(data.get("agent_type"))):
            return 0  # not a reviewer session
        root = NO_WORKTREE  # a reviewer the driver did not start: report files only
    bad = malformed(data)
    if bad:
        print(bad, file=sys.stderr)
        return 2
    why = verdict(data, root)
    if why:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
