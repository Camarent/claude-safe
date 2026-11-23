"""Public-words scan: which git-tracked markdown lines contain a forbidden word (case-insensitive, whole text, not regex)."""

import os
import re

from . import gitx
from .configuration import GIT_QUICK_TIMEOUT_S, GIT_SAFE_ENV, PUBLIC_DOC_GLOB

_HIT = "{file}:{line}: {word}"  # one hit line: path, 1-based line number, the word found


def hits(repo, words):
    """-> ["file:line: word", ...] for every tracked *.md file of `repo` line that contains a word of `words` (case-insensitive). Empty words -> []. Blank words are ignored: an empty pattern would match every line."""
    needles = [w for w in words if w and w.strip()]
    if not needles:
        return []
    listed = gitx.try_run(repo, "ls-files", "--", PUBLIC_DOC_GLOB, timeout=GIT_QUICK_TIMEOUT_S, env={**os.environ, **GIT_SAFE_ENV})
    if listed is None or listed.returncode != 0:
        return []
    found = []
    for rel in listed.stdout.splitlines():
        try:
            with open(os.path.join(repo, rel), encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for n, text in enumerate(lines, 1):
            for w in needles:
                if re.search(re.escape(w), text, re.IGNORECASE):
                    found.append(_HIT.format(file=rel, line=n, word=w))
    return found
