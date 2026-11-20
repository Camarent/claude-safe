"""The rule for the command `servectl start -- <command>` may run: one shared check for the tool (servectl.py, the enforcement) and the guard (cmd_guard.py, a pre-check)."""

import os

from .configuration import (
    SERVE_DEFAULT_COMMANDS,
    SERVE_DIR_FLAG,
    SERVE_DIR_TOKEN,
    SERVE_PORT_TOKEN,
    SERVE_SHELL_META,
    SERVE_SHELLS,
    ConfigError,
    project,
)


def allowed_commands(workspace):
    """-> the command prefixes servectl may start in WORKSPACE: SERVE_DEFAULT_COMMANDS plus the project's pipeline.json `serve_commands`. Raises ConfigError."""
    extra = project(workspace).get("serve_commands") or []
    return tuple(SERVE_DEFAULT_COMMANDS) + tuple(str(p) for p in extra)


def _word_ok(word, token):
    """-> True when ARGV word WORD fits the prefix word TOKEN: the port placeholder takes digits, the directory placeholder a path without `..` that is not an option, anything else must be equal."""
    if token == SERVE_PORT_TOKEN:
        return word.isdigit()
    if token == SERVE_DIR_TOKEN:
        return bool(word) and not word.startswith("-") and ".." not in word.split("/")
    return word == token


def matches(argv, prefix):
    """-> True when the words of ARGV start with the words of PREFIX. A placeholder word (port, directory) takes exactly one word; a prefix ending in a placeholder allows nothing after it."""
    words = prefix.split()
    if len(argv) < len(words) or not all(_word_ok(w, t) for w, t in zip(argv, words, strict=False)):
        return False
    return len(argv) == len(words) if words and words[-1] in (SERVE_PORT_TOKEN, SERVE_DIR_TOKEN) else True


def command_why(argv, prefixes):
    """-> deny reason when the inner command ARGV may not run under servectl (not a prefix, a shell operator, a shell run with -c), else ''."""
    if not argv:
        return "the server command after `--` is empty"
    if any(m in w for w in argv for m in SERVE_SHELL_META):
        return "the server command may not contain shell operators or substitution (; & | $( ` > < or a newline): servectl runs no shell."
    for i, w in enumerate(argv[:-1]):
        if os.path.basename(w) in SERVE_SHELLS and argv[i + 1].startswith("-") and "c" in argv[i + 1][1:]:
            return "the server command may not run a shell with -c (sh -c, bash -c): servectl runs no shell."
    if any(matches(argv, p) for p in prefixes):
        return ""
    return (
        f"servectl start runs only these server commands: {', '.join(prefixes)} (a `<port>` prefix takes one port number). "
        "Add the project's own server command to pipeline.json `serve_commands`."
    )


def _outside_workspace(argv, workspace):
    """-> deny reason when ARGV serves a folder (`--directory <dir>`) that is not inside WORKSPACE (a link is followed), else ''. The workspace itself is fine; `/` or another project is not.
    The server uses the last `--directory`, so a repeated flag, the `--directory=<dir>` form and a flag with no value are refused."""
    if not any(w == SERVE_DIR_FLAG or w.startswith(SERVE_DIR_FLAG + "=") for w in argv):
        return ""
    if argv.count(SERVE_DIR_FLAG) != 1 or any(w.startswith(SERVE_DIR_FLAG + "=") for w in argv) or argv[-1] == SERVE_DIR_FLAG:
        return f"servectl takes `{SERVE_DIR_FLAG} <folder>` once, as two words."
    root = os.path.realpath(workspace)
    folder = os.path.realpath(os.path.join(workspace, argv[argv.index(SERVE_DIR_FLAG) + 1]))
    if folder == root or folder.startswith(root + os.sep):
        return ""
    return f"servectl serves a folder only inside the workspace ({root}); {folder} is outside."


def start_refusal(argv, workspace):
    """-> deny reason for `servectl start -- ARGV` run in WORKSPACE (the cwd), else ''. A pipeline.json that does not load refuses too."""
    try:
        prefixes = allowed_commands(workspace)
    except ConfigError as e:
        return f"cannot read the project's pipeline.json ({e}); fix it first."
    return command_why(list(argv), prefixes) or _outside_workspace(list(argv), workspace)
