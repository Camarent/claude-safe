#!/usr/bin/env python3
"""Fair concurrency limiter for the local model, shared by every process in the container (file locks, no server).

    from search.localq import slot
    with slot("call"):   # one model request       (LOCALQ_CALLS slots, default 1: one request fits the 128k window; overlapping calls ran ~2x slower each in a past run log)
        ...
    with slot("job"):    # one whole claude -p job (LOCALQ_JOBS slots, default 2)
        ...
CLI:  python3 -I localq.py run [--kind job] -- <command>    run a command inside a slot
      python3 -I localq.py status                            who holds what
Waiters poll every 0.2-1 s and take the first free slot; a held slot is released when the process ends, even if it crashes (flock).
LOCALQ_COOLDOWN (seconds, default 0) sleeps AFTER each slot is released, spacing heavy jobs to let the machine cool.
"""

import contextlib
import fcntl
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # run as a script: tools dir is not on the path
from pipeline.configuration import SLOT_TIMEOUT_S
from pipeline.util import local_log

DIR = os.environ.get("LOCALQ_DIR", "/tmp/localq")  # nosec  # per-uid path in a single-user container


def limits():
    return {"call": int(os.environ.get("LOCALQ_CALLS", "1")), "job": int(os.environ.get("LOCALQ_JOBS", "2"))}


@contextlib.contextmanager
def slot(kind="call", timeout=SLOT_TIMEOUT_S):
    os.makedirs(DIR, exist_ok=True)
    n = limits()[kind]
    deadline = time.time() + timeout
    held = None
    unopenable = 0
    waited = False
    while held is None:
        for i in random.sample(range(n), n):  # random start spreads waiters over the slots  # nosec B311  # jitter, not security
            try:
                f = open(os.path.join(DIR, f"{kind}{i}.lock"), "a+")  # noqa: SIM115  # handle outlives this function
            except OSError:  # lock file owned by another uid (container leftover): skip this slot
                unopenable += 1
                continue
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                f.seek(0)
                f.truncate()
                f.write(f"{os.getpid()} {os.path.basename(sys.argv[0])} {int(time.time())}\n")
                f.flush()
                held = f
                break
            except OSError:
                f.close()
        if held is None and unopenable >= n:  # no lock file can be opened at all: run unlocked rather than wait forever
            print(f"localq: no {kind} lock file is writable in {DIR}; running without a slot", file=sys.stderr)
            break
        if held is None:
            if time.time() > deadline:
                raise TimeoutError(f"localq: no {kind} slot within {timeout}s")
            unopenable = 0
            if not waited:
                waited = True
                local_log(f"localq waiting for a free {kind} slot (all {n} busy, timeout {timeout}s)")
            time.sleep(0.2 + random.random() * 0.4)  # nosec B311  # jitter, not security
    try:
        yield
    finally:
        if held is not None:
            fcntl.flock(held, fcntl.LOCK_UN)
            held.close()
        cd = float(os.environ.get("LOCALQ_COOLDOWN", "0"))
        if cd:
            time.sleep(cd)


def status():
    out = []
    for kind, n in limits().items():
        for i in range(n):
            p = os.path.join(DIR, f"{kind}{i}.lock")
            if not os.path.exists(p):
                out.append(f"{kind}{i}: free")
                continue
            with open(p, "a+", encoding="utf-8") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    out.append(f"{kind}{i}: free")
                    fcntl.flock(f, fcntl.LOCK_UN)
                except OSError:
                    f.seek(0)
                    out.append(f"{kind}{i}: busy ({f.read().strip()})")
    return "\n".join(out)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] == "status":
        print(status())
        return 0
    if a[0] == "run":
        kind = a[a.index("--kind") + 1] if "--kind" in a else "job"
        cmd = a[a.index("--") + 1 :]
        with slot(kind):
            return subprocess.call(cmd)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
