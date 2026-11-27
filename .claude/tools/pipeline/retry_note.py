"""Lint of the note a `retry --note` gives the dev: the note must name the file and line to change (`path[:line]`), and a path outside the task's Predicted paths, or one its Done/Test lines forbid, is a warning."""

import re

from . import task_text

# a path token: optional folders, then either a name with an extension and an optional `:line`,
# or an extensionless name (`Makefile:12`, `scripts/dev:3`) that must carry a `:line` (so `10:30` is no path)
PATH_RE = re.compile(r"(?<![\w/.-])(?:[\w.-]+(?:/[\w.-]+)*\.\w+(?::\d+)?|[A-Za-z_][\w.-]*(?:/[\w.-]+)*:\d+)(?![\w/-])")
KNOWN_EXT = frozenset(
    [
        "py",
        "sh",
        "md",
        "json",
        "jsonl",
        "js",
        "mjs",
        "ts",
        "tsx",
        "jsx",
        "toml",
        "yml",
        "yaml",
        "txt",
        "cfg",
        "ini",
        "rs",
        "go",
        "c",
        "h",
        "cpp",
        "java",
        "html",
        "css",
        "sql",
        "lock",
    ]
)
NEGATION_RE = re.compile(r"\b(?:not|never|forbid\w*|must not|do not|don't)\b", re.I)
ALLOWED_FORM = 'retry --note "edit <path>:<line> <what to change>"'


def _is_path(token) -> bool:
    """True when TOKEN looks like a file: it has a folder part, a `:line` suffix or a known extension (so `2.0x`, `e.g`, `v1.2` are not paths)."""
    return "/" in token or ":" in token or token.rsplit(".", 1)[-1].lower() in KNOWN_EXT


def _paths(text) -> list[str]:
    """-> the path tokens of TEXT, without their `:line` part, in order."""
    return [m[0].split(":", 1)[0] for m in PATH_RE.finditer(text) if _is_path(m[0])]


def _same(a, b) -> bool:
    """True when path tokens A and B name the same file (one is the tail of the other: `pipeline/x.py` and `.claude/tools/pipeline/x.py`)."""
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _predicted(task) -> list[str]:
    """-> the Predicted paths of the task file text TASK."""
    return [p for line in task_text.field(task, "Predicted paths") for p in _paths(line)]


def _forbidden(task) -> list[str]:
    """-> the path tokens that a Done or Test line names in a negated clause (`do not edit X`)."""
    out = []
    for line in task_text.lines(task, "Done", "Test"):
        for clause in re.split(r";|\.\s", line[1]):  # a dot inside a path (`cli_orch.py`) does not end the clause
            if NEGATION_RE.search(clause):
                out += _paths(clause)
    return out


def refused(note) -> bool:
    """True when NOTE names no path[:line]: the retry is refused."""
    return not _paths(note)


def lint(note, task_text_value) -> list[str]:
    """-> messages for the retry NOTE given the task file text TASK_TEXT: the refusal (no path token, one message) or advisory warnings."""
    if refused(note):
        return [f"orch: retry note refused: it names no file (path[:line]). Allowed form: {ALLOWED_FORM}"]
    out = []
    predicted = _predicted(task_text_value)
    forbidden = _forbidden(task_text_value)
    for p in dict.fromkeys(_paths(note)):
        if any(_same(p, f) for f in forbidden):
            out.append(f"orch: warning: retry note names {p}, which the task's Done/Test lines forbid")
        elif predicted and not any(_same(p, q) for q in predicted):
            out.append(f"orch: warning: retry note names {p}, outside the task's Predicted paths")
    return out
