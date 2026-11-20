"""gatewatch: what a task gate run changed outside its job worktree, and the data that explains a red gate.

Task gates of several tasks run at the same time, each in its own worktree and with its own TMPDIR (qrun). A test that writes a fixed path below the main checkout is shared by all of them and collides. `snapshot` records the files of the `gate_watch` folders before and after a gate run; `changes` names what appeared, vanished or changed; `offenders` names the test files that mention those paths; `environment` is the block a red gate report carries.
"""

import os
import time

from pipeline.configuration import GATE_ENV_NAMES, GATE_ENV_PREFIXES, GATE_WATCH_FILE_BYTES, GATE_WATCH_GREP_FILES, GATE_WATCH_MAX_ENTRIES, GATE_WATCH_SHOW


def snapshot(root, rels):
    """-> {path relative to ROOT: (mtime_ns, size)} for every file below the REL folders (at most GATE_WATCH_MAX_ENTRIES; a cut folder adds the key `...`)."""
    snap: dict[str, tuple[int, int]] = {}
    for rel in rels:
        for base, dirs, files in os.walk(os.path.join(root, rel)):
            for name in dirs + files:
                full = os.path.join(base, name)
                if len(snap) >= GATE_WATCH_MAX_ENTRIES:
                    snap["..."] = (0, 0)
                    return snap
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                snap[os.path.relpath(full, root)] = (st.st_mtime_ns, st.st_size)
    return snap


def changes(before, after):
    """-> sorted [(path, 'created' | 'removed' | 'changed')] between two snapshots."""
    out = [(p, "created") for p in after if p not in before]
    out += [(p, "removed") for p in before if p not in after]
    out += [(p, "changed") for p in before if p in after and before[p] != after[p]]
    return sorted(out)


def _grep_files(worktree, pats, git):
    """-> the files that name any pattern: ONE `git grep -l -z -F` call (NUL-separated, so a file name with `:` or a newline stays whole), at most GATE_WATCH_GREP_FILES."""
    args = [x for n in pats for x in ("-e", n)]
    text = git(worktree, "grep", "-l", "-z", "-F", "-I", *args) or ""
    return [f for f in text.split("\0") if f][:GATE_WATCH_GREP_FILES]


def _text_of(worktree, rel):
    """-> the first GATE_WATCH_FILE_BYTES of a worktree file ("" when unreadable)."""
    path = os.path.join(worktree, rel)
    if os.path.islink(path) or not os.path.isfile(path):  # a link could point outside the worktree, a special file could block
        return ""
    try:
        with open(path, "rb") as fh:
            return fh.read(GATE_WATCH_FILE_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def offenders(worktree, paths, git):
    """-> {path: [files in the worktree that name the path's last component]}; `git(repo, *args)` -> text or None is injectable. One git call for all paths, then only the files it names are read."""
    names = {p: os.path.basename(p) for p in paths}
    pats = sorted({n for n in names.values() if n})
    if not pats:
        return {}
    texts = {f: _text_of(worktree, f) for f in _grep_files(worktree, pats, git)}
    found: dict[str, list[str]] = {}
    for p, n in names.items():
        files = [f for f, t in texts.items() if n and n in t]
        if files:
            found[p] = files
    return found


def warning(worktree, before, after, git):
    """-> the report text for what the gate run changed in the watched folders; "" when nothing changed."""
    if "..." in before or "..." in after:
        return f"warning: a `gate_watch` folder holds more than {GATE_WATCH_MAX_ENTRIES} entries: the gate run was not compared"
    diff = changes(before, after)
    if not diff:
        return ""
    lines = [
        f"warning: the gate run changed files in `gate_watch` folders outside its worktree ({len(diff)}); another job may have done it, a test that writes a fixed path collides when jobs run together"
    ]
    named = offenders(worktree, [p for p, _ in diff[:GATE_WATCH_SHOW]], git)
    for p, how in diff[:GATE_WATCH_SHOW]:
        lines.append(f"  {how}: {p}" + (f" (named in {', '.join(named[p][:3])})" if p in named else ""))
    if len(diff) > GATE_WATCH_SHOW:
        lines.append(f"  ... and {len(diff) - GATE_WATCH_SHOW} more")
    return "\n".join(lines)


def environment(env, cwd, hooks_path, extra=()):
    """-> the block a red gate report carries: the time, the working directory, core.hooksPath of the worktree and the chosen environment variables (name=value, sorted: GATE_ENV_NAMES, GATE_ENV_PREFIXES and EXTRA, the project's `test_env` names)."""
    keep = sorted(k for k in env if k in GATE_ENV_NAMES or k in extra or k.startswith(GATE_ENV_PREFIXES))
    lines = [
        "gate process (qrun gives each command its own TMPDIR):",
        f"  time: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"  cwd: {cwd}",
        f"  git core.hooksPath: {hooks_path or '(unset)'}",
    ]
    return "\n".join(lines + [f"  {k}={env[k]}" for k in keep])
