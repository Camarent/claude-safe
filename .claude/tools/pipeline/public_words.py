"""Public-words scan: which git-tracked text lines contain a forbidden word (case-insensitive, whole text, not regex)."""

import os
import re

from . import gitx
from .configuration import GIT_QUICK_TIMEOUT_S, GIT_SAFE_ENV, PUBLIC_BINARY_SNIFF_BYTES

_HIT = "{file}:{line}: {word}"  # one hit line: path, 1-based line number, the word found


class ScanError(Exception):
    """The tracked files could not be listed, so the scan cannot say it is clean."""


def hits(repo, words) -> list[str]:
    """-> ["file:line: word", ...] for every line of every tracked text file of `repo` that contains a word of `words` (case-insensitive). Raises ScanError as records() does."""
    return [_HIT.format(file=rel, line=n, word=w) for rel, n, w, _text in records(repo, words)]


def hit_key(rel, word, text) -> str:
    """-> the baseline key of one hit: `file TAB word TAB line text stripped`. No line number: the key stays the same when lines above the hit move."""
    return f"{rel}\t{word}\t{text.strip()}"


def read_baseline(path) -> set[str]:
    """-> set of hit keys listed in the baseline file at `path`. Blank lines and lines starting with # are ignored. A missing file is an empty baseline. Raises ScanError when the file exists but cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return set()
    except OSError:
        raise ScanError(f"scan failed: cannot read baseline {path}") from None
    return {s for s in (line.strip() for line in lines) if s and not s.startswith("#")}


def diff(found, baseline) -> tuple[list[tuple[str, int, str, str]], list[str]]:
    """-> (new, stale): `new` are the records of `found` whose hit key is not in `baseline`, in scan order; `stale` are the baseline keys no record matches, sorted. A key listed once covers every identical line."""
    new = []
    seen = set()
    for rec in found:
        key = hit_key(rec[0], rec[2], rec[3])
        seen.add(key)
        if key not in baseline:
            new.append(rec)
    return new, sorted(baseline - seen)


def records(repo, words, skip=()) -> list[tuple[str, int, str, str]]:
    """-> [(file, line, word, text), ...] for every line of every tracked text file of `repo` that contains a word of `words` (case-insensitive). `line` is 1-based, `text` the raw line. Empty words -> []. Blank words are ignored: an empty pattern would match every line; the rest are stripped. Binary files (NUL byte in the first PUBLIC_BINARY_SNIFF_BYTES) and the repo-relative files in `skip` are not scanned. Raises ScanError when git cannot list the tracked files."""
    needles = [w.strip() for w in words if w and w.strip()]
    if not needles:
        return []
    # -z: NUL-separated, names kept raw (git would quote non-ASCII names and the open() would fail)
    listed = gitx.try_run(repo, "ls-files", "-z", timeout=GIT_QUICK_TIMEOUT_S, env={**os.environ, **GIT_SAFE_ENV})
    if listed is None or listed.returncode != 0:
        raise ScanError("scan failed: git could not list the tracked files")
    found = []
    for rel in filter(None, listed.stdout.split("\0")):
        if rel in skip:
            continue
        # a tracked symlink names a target outside the repo: it is not repo text, so it is not scanned
        if os.path.islink(os.path.join(repo, rel)):
            continue
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
                    found.append((rel, n, w, text))
    return found
