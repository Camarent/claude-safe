"""Plan-review gaps still open in a stage or feature folder: the lines of GAPS_FILE or PLAN_REVIEW_GAPS_FILE, read at stage start so the lead sees them."""

import os

from .configuration import GAPS_FILE, PLAN_REVIEW_GAPS_FILE


def open_gaps(folder) -> list:
    """-> the non-blank lines of GAPS_FILE and PLAN_REVIEW_GAPS_FILE found in FOLDER (GAPS_FILE first), [] when neither exists."""
    lines: list[str] = []
    for name in (GAPS_FILE, PLAN_REVIEW_GAPS_FILE):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:  # a stray non-UTF-8 byte must not abort the run
            lines.extend(ln.rstrip() for ln in f if ln.strip())
    return lines
