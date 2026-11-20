#!/usr/bin/env python3
"""ctxpack: print numbered slices to paste into a brief. Usage: ctxpack.py path:START-END [path:N ...]. Slice cap 80 lines."""

import sys


def pack(spec, cap=80):
    path, _, rng = spec.rpartition(":")
    a, _, b = rng.partition("-")
    a = int(a)
    b = int(b) if b else a
    b = min(b, a + cap - 1)
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    out = [f"## {path}:{a}-{b}"]
    out += [f"{i}\t{lines[i - 1]}" for i in range(a, min(b, len(lines)) + 1)]
    return "\n".join(out)


if __name__ == "__main__":
    for s in sys.argv[1:]:
        print(pack(s))
