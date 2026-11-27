"""One git runner for the pipeline: `run` -> CompletedProcess, `out` raises on failure, `try_out` -> None on failure.
Every call carries the safe.directory setting (repos may be owned by another uid) and a timeout."""

import re
import subprocess

from .configuration import GIT_SAFE_DIR_ARGS, GIT_TIMEOUT_S


def run(repo, *args, timeout=GIT_TIMEOUT_S, input=None, env=None) -> subprocess.CompletedProcess:
    """git -C repo args -> CompletedProcess (INPUT goes to stdin, ENV replaces the environment when given; text, never raises on a non-zero exit; a timeout raises subprocess.TimeoutExpired)."""
    return subprocess.run(
        ["git", *GIT_SAFE_DIR_ARGS, "-C", repo, *args], capture_output=True, text=True, errors="replace", timeout=timeout, check=False, input=input, env=env
    )


def raw_out(repo, *args, timeout=GIT_TIMEOUT_S) -> bytes:
    """stdout of a git call as BYTES (a diff must keep CRLF and non-UTF-8 text as they are; `run` decodes). Raises subprocess.CalledProcessError on a non-zero exit, subprocess.TimeoutExpired on a timeout."""
    return subprocess.run(["git", *GIT_SAFE_DIR_ARGS, "-C", repo, *args], capture_output=True, timeout=timeout, check=True).stdout


def out(repo, *args, timeout=GIT_TIMEOUT_S, check=True) -> str:
    """stdout (trailing newline stripped) of a git call; RuntimeError with git's own message when it times out or (check) fails."""
    try:
        r = run(repo, *args, timeout=timeout)
    except subprocess.TimeoutExpired as e:  # a merge killed mid-way may leave a nested repo half-merged: say so, do not trace back
        raise RuntimeError(f"git {' '.join(args)} in {repo} timed out after {e.timeout:.0f} s; check the repo for a half-done merge before retrying") from e
    if check and r.returncode:
        msg = "\n".join(x.strip() for x in (r.stdout, r.stderr) if x.strip())  # a pre-commit hook (gitleaks) writes to stdout; git's own error comes last
        raise RuntimeError(f"git {' '.join(args)} in {repo}: {msg[-500:]}")
    return r.stdout.rstrip("\n")  # keep leading spaces: ` M path` from `status --porcelain` is a column layout


def try_out(repo, *args, timeout=GIT_TIMEOUT_S) -> str | None:
    """stdout of a git call, None when it fails (callers decide what a failure means)."""
    r = run(repo, *args, timeout=timeout)
    return r.stdout if r.returncode == 0 else None


def try_run(repo, *args, timeout=GIT_TIMEOUT_S, env=None) -> subprocess.CompletedProcess | None:
    """`run`, but None when git cannot start or times out (no exception)."""
    try:
        # env only when given: tests replace `run` with fakes that take no env
        return run(repo, *args, timeout=timeout, **({} if env is None else {"env": env}))
    except (OSError, subprocess.SubprocessError):
        return None


def try_text(repo, *args, timeout=GIT_TIMEOUT_S) -> str:
    """stdout of a git call that exited 0, else "" (also on a timeout or when git cannot run)."""
    r = try_run(repo, *args, timeout=timeout)
    return r.stdout if r and r.returncode == 0 else ""


def ignored(repo, paths) -> list[str]:
    """-> the PATHS git ignores in REPO (`git check-ignore`, one call per path). Tracked files and paths outside REPO are not reported: a path git rejects hides no other path."""
    hits = []
    for p in paths:
        r = try_run(repo, "check-ignore", "-q", "--", p)
        # exit 0: ignored; exit 1: not ignored; anything else (outside REPO, no work tree, no git): not reported
        if r and r.returncode == 0:
            hits.append(p)
    return hits


_C_ESCAPES = {"t": "\t", "n": "\n"}
_ESCAPE_RE = re.compile(r"\\([0-7]{3}|.)", re.S)
_OCTAL_BASE = 8
_OCTAL_DIGITS = 3


def unquote_path(quoted) -> str:
    """git's C-quoted path (with the double quotes) -> the real path; octal escapes are UTF-8 bytes."""
    body = quoted[1:-1] if quoted.startswith('"') and quoted.endswith('"') else quoted
    raw = bytearray()
    pos = 0
    for m in _ESCAPE_RE.finditer(body):
        raw += body[pos : m.start()].encode()
        esc = m[1]
        raw += int(esc, _OCTAL_BASE).to_bytes(1, "big") if len(esc) == _OCTAL_DIGITS else _C_ESCAPES.get(esc, esc).encode()
        pos = m.end()
    raw += body[pos:].encode()
    return raw.decode(errors="replace")


def _diff_path(raw):
    """-> repo path of the `+++ ` side of a diff header ("" for /dev/null); handles git's quoted form and the trailing tab of names with spaces."""
    raw = raw.rstrip("\t")
    if raw.startswith('"') and raw.endswith('"'):
        raw = unquote_path(raw)
    return raw[2:] if raw.startswith("b/") else ""


def added_lines(diff_text) -> dict[str, list[tuple[int, str]]]:
    """-> {file: [(line number, text without the "+")]} for the added lines of a `git diff -U0` text. Every file with a `+++ b/` header is a key (deletions only -> []); deleted files are left out."""
    out: dict = {}
    cur, line = "", 0
    for ln in diff_text.splitlines():
        if ln.startswith("+++ "):
            cur = _diff_path(ln[4:])
            if cur:
                out.setdefault(cur, [])
        elif m := re.match(r"@@ -\S+ \+(\d+)", ln):
            line = int(m[1]) - 1
        elif ln.startswith("+") and cur:
            line += 1
            out[cur].append((line, ln[1:]))
    return out
