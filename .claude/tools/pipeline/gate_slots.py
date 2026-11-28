"""Host-wide cap on parallel gate runs: `gate_slots` flock slot files under one folder per user in the system temp dir. A gate command holds one slot while it runs; `held` blocks until a slot is free. The kernel frees a slot when its holder dies, so a crashed run never keeps a slot taken. Slot files are never removed: an open file nobody holds costs nothing. Tests that reach `held` through the default folder leave the files in the temp dir they set up (tests/conftest.py and ptest.py remove that dir at the end of the run)."""

import contextlib
import fcntl
import os
import tempfile
import time
from collections.abc import Callable, Generator

from pipeline.configuration import GATE_SLOT_DIR, GATE_SLOT_POLL_S


def slot_dir() -> str:
    """-> the folder of the slot files: one per user, so another user's files never block this one (0o644 files are not writable by others)."""
    return os.path.join(tempfile.gettempdir(), GATE_SLOT_DIR, str(os.getuid()))


def _try_slot(path: str) -> int | None:
    """-> the open fd holding the flock of slot file `path`, or None when another holder has it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # EWOULDBLOCK: another open file description holds this slot
        os.close(fd)
        return None
    return fd


def _take(paths: list[str]) -> int | None:
    """-> the fd of the first free slot in `paths`, or None when all are held."""
    for path in paths:
        fd = _try_slot(path)
        if fd is not None:
            return fd
    return None


@contextlib.contextmanager
def held(n: int, base: str | None = None, sleep: Callable[[float], None] = time.sleep) -> Generator[None]:
    """Hold one of the `n` slots under `base` (default slot_dir()) for the with-block. Waits (calls `sleep` every GATE_SLOT_POLL_S) until any slot is free. `n` <= 0 = no cap: nothing is held."""
    if n <= 0:
        yield
        return
    folder = base or slot_dir()
    os.makedirs(folder, exist_ok=True)
    paths = [os.path.join(folder, f"slot-{i}") for i in range(n)]
    fd = _take(paths)
    while fd is None:  # all held: retry every slot, so any slot that frees wakes the waiter
        sleep(GATE_SLOT_POLL_S)
        fd = _take(paths)
    try:
        yield
    finally:
        os.close(fd)  # closing the fd drops the flock
