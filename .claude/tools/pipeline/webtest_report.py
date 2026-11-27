"""webtest_report: the browser-check report of a stage, stamped with the HEAD it was run on, and the staleness check against the branch HEAD."""

import re
from pathlib import Path

RESULTS = ("PASS", "FAIL", "BLOCKED")
# shortest sha that may stand for a full one: shorter prefixes match too many commits
MIN_SHA_LEN = 7
SHA_RE = re.compile(rf"[0-9a-fA-F]{{{MIN_SHA_LEN},}}")


def write(path: str, result: str, sha: str, date: str, worktree: str, body: str = "") -> None:
    """Write the report at PATH: RESULT, HEAD and DATE lines, then BODY. Refuses a RESULT other than PASS|FAIL|BLOCKED and a PATH outside WORKTREE."""
    if result not in RESULTS:
        raise ValueError(f"webtest result must be one of {', '.join(RESULTS)}, got {result!r}")
    target = Path(path).resolve()
    root = Path(worktree).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"webtest report outside the worktree: {target} is not under {root}")
    target.parent.mkdir(parents=True, exist_ok=True)
    head = f"RESULT: {result}\nHEAD: {sha}\nDATE: {date}\n"
    target.write_text(head + body, encoding="utf-8")


def head_line(text: str) -> str | None:
    """-> the sha of the first `HEAD: <sha>` line outside code fences (a line start, not mid-line), or None."""
    fenced = False
    for raw in text.splitlines():
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or not raw.startswith("HEAD: "):
            continue
        sha = raw[len("HEAD: ") :].strip()
        if sha:
            return sha
    return None


def _same_commit(sha: str, head: str) -> bool:
    """True when one of SHA, HEAD is a hex prefix of the other, at least MIN_SHA_LEN long."""
    short, long = sorted((sha, head), key=len)
    return SHA_RE.fullmatch(short) is not None and long.startswith(short)


def stale(text: str, head: str) -> str | None:
    """-> the reason the report at HEAD is stale (a string), or None when its HEAD matches the branch HEAD."""
    sha = head_line(text)
    if sha is None:
        return "stale webtest: no HEAD line"
    if _same_commit(sha, head):
        return None
    return f"stale webtest: report HEAD {sha}, branch HEAD {head}"
