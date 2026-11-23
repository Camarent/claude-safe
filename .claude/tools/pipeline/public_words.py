"""Public-words scan: which git-tracked text lines contain a forbidden word (case-insensitive, whole text, not regex)."""

import os
import re

from . import gitx
from .configuration import GIT_QUICK_TIMEOUT_S, GIT_SAFE_ENV, PUBLIC_BINARY_SNIFF_BYTES

_HIT = "{file}:{line}: {word}"  # one hit line: path, 1-based line number, the word found


class ScanError(Exception):
    """The tracked files could not be listed, so the scan cannot say it is clean."""


def hits(repo, words):
    """-> ["file:line: word", ...] for every line of every tracked text file of `repo` that contains a word of `words` (case-insensitive). Empty words -> []. Blank words are ignored: an empty pattern would match every line; the rest are stripped. Binary files (NUL byte in the first PUBLIC_BINARY_SNIFF_BYTES) are skipped. Raises ScanError when git cannot list the tracked files."""
    needles = [w.strip() for w in words if w and w.strip()]
    if not needles:
        return []
    # -z: NUL-separated, names kept raw (git would quote non-ASCII names and the open() would fail)
    listed = gitx.try_run(repo, "ls-files", "-z", timeout=GIT_QUICK_TIMEOUT_S, env={**os.environ, **GIT_SAFE_ENV})
    if listed is None or listed.returncode != 0:
        raise ScanError("scan failed: git could not list the tracked files")
    found = []
    for rel in filter(None, listed.stdout.split("\0")):
        try:
            with open(os.path.join(repo, rel), "rb") as f:
                head = f.read(PUBLIC_BINARY_SNIFF_BYTES)
                if b"\0" in head:
                    continue
                raw = head + f.read()
        except OSError:
            raise ScanError(f"scan failed: cannot read tracked file {rel}") from None
        for n, text in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            for w in needles:
                if re.search(re.escape(w), text, re.IGNORECASE):
                    found.append(_HIT.format(file=rel, line=n, word=w))
    return found
