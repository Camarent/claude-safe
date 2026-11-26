"""The docs-count rule: a task that writes a machine count or a line number into a hand-written doc is an error (`docs-count:`)."""

import os
import re

from .configuration import DOCS_COUNT_MSG, DOCS_COUNT_PREFIX, DOCS_COUNT_RE, DOCS_INDEX_PATH, HAND_DOC_DIRS, HAND_DOC_NAMES
from .plan_files import paths_in
from .plan_rules import _is_docs_path
from .util import read_text


def _is_hand_written(p):
    """True for a Predicted path that is a hand-written doc: a .md path under a HAND_DOC_DIRS folder or named in HAND_DOC_NAMES, but not the generated DOCS_INDEX_PATH."""
    if not _is_docs_path(p) or p == DOCS_INDEX_PATH:
        return False
    return p.startswith(HAND_DOC_DIRS) or os.path.basename(p) in HAND_DOC_NAMES


def check_docs_counts(d, tid) -> list[str]:
    """`docs-count:` error: a Predicted path is a hand-written doc and a Goal or Done line of the task matches DOCS_COUNT_RE (a count or a line number). Counts go to the generated docs index instead; one line per task."""
    text = read_text(os.path.join(d, tid + ".md"))
    if not any(_is_hand_written(p) for p in paths_in(text)):
        return []
    goal_done = "\n".join(ln for ln in text.splitlines() if ln.startswith(("Goal:", "Done:")))
    if not re.search(DOCS_COUNT_RE, goal_done):
        return []
    return [f"{DOCS_COUNT_PREFIX} {tid}: its Goal or Done pins a count or line number in a hand-written doc: {DOCS_COUNT_MSG}"]
