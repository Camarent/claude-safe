"""Scratch dirs named `<prefix><pid>-<random>` (qrun TMPDIR): the pid in the name tells the leftover of a dead process from the dir of a live one."""

import contextlib
import os


def owner_pid(name, prefix):
    """-> the pid in NAME (`<prefix><pid>-...`), or None when NAME has another shape."""
    if not name.startswith(prefix):
        return None
    head = name[len(prefix) :].split("-", 1)[0]
    return int(head) if head.isdigit() and int(head) > 0 else None


def pid_alive(pid):
    """True while PID exists (also when another user owns it: it is still not ours to remove)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def dead_owner_dirs(root, prefix):
    """-> paths of real dirs in ROOT named `<prefix><pid>-...` whose pid is no longer alive; [] when ROOT cannot be listed."""
    out = []
    with contextlib.suppress(OSError):
        for e in os.scandir(root):
            pid = owner_pid(e.name, prefix)
            if pid is not None and e.is_dir(follow_symlinks=False) and not pid_alive(pid):
                out.append(e.path)
    return out
