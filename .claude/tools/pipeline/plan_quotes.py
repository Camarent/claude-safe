"""The quote-exists rule (an error): a Done line that quotes a message (`warning 'xxx'`, `message "xxx"`, `text `xxx`` or `prints ...`, the quote at least QUOTE_MIN_LEN characters) must find that text in a tracked file, or the task text must say it creates it. Only the Done lines are read: a quote in a Test line (a code span) is never checked."""

import os
import re

from . import gitx
from .configuration import QUOTE_MIN_LEN, QUOTE_MISSING_PREFIX, QUOTE_WORDS
from .plan_check_task import _repo_root
from .task_text import lines
from .util import read_text

# a Done word, then a quoted string (', " or backtick) of at least QUOTE_MIN_LEN characters on the same line
QUOTE_RE = re.compile(
    r"\b(?:" + "|".join(QUOTE_WORDS) + r")\b[^\n'\"`]*?[\"'`]([^\"'`\n]{" + str(QUOTE_MIN_LEN) + r",})[\"'`]",
    re.I,
)


def _text(d, tid) -> str:
    return read_text(os.path.join(d, tid + ".md"))


def _quoted(done) -> list[str]:
    """-> the distinct quoted messages of the Done value DONE, in order of first use."""
    out: list[str] = []
    for m in QUOTE_RE.finditer(done):
        if m[1] not in out:
            out.append(m[1])
    return out


def _in_tree(repo, text) -> bool:
    """True when some tracked file of REPO contains TEXT (a fixed string, git grep)."""
    return bool(gitx.try_text(repo, "grep", "-l", "-F", "-e", text).strip())


def _creates(body, text) -> bool:
    """True when the task text outside its Done and Test lines spells TEXT out: the task creates it."""
    return text in body


def check_quotes(d, ents, repo=None) -> list[str]:
    """`quote-missing:` errors for the entries of ENTS whose Done quotes a message that no tracked file of REPO holds and that the task text does not create."""
    repo = repo or _repo_root(d)
    probs = []
    for e in ents:
        text = _text(d, e["id"])
        body = _outside(text)
        for quoted in _quoted(" ".join(value for _, value in lines(text, "Done"))):
            if _creates(body, quoted) or _in_tree(repo, quoted):
                continue
            probs.append(f"{QUOTE_MISSING_PREFIX} {e['id']}: Done quotes '{quoted}' but no tracked file has it and the task does not create it")
    return probs


def _outside(text) -> str:
    """-> TEXT without its Done and Test field lines (the rest of the task text, where a task spells out what it creates)."""
    return "\n".join(line for line in text.splitlines() if not re.match(r"(?:Done|Test):", line))
