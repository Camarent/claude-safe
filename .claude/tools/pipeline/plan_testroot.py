"""plan_check rule: a `Test:` line gives a path that repeats the root of its test runner (the runner runs inside that root)."""

import os

from .configuration import TEST_ROOT_PREFIX, TEST_ROOTS_KEY, project
from .plan_testcmd import TEST_LINE_RE
from .shellwords import split_commands
from .util import read_text


def _root_entries(cfg) -> list[tuple[str, str]]:
    """-> [(runner word, root without slashes)] from cfg `test_roots`; a malformed entry (not a dict, no runner or root text, an empty root) is ignored."""
    raw = cfg.get(TEST_ROOTS_KEY) or []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("runner"), str) or not isinstance(item.get("root"), str):
            continue
        root = item["root"].strip().strip("/")
        if item["runner"].strip() and root:
            out.append((item["runner"].strip(), root))
    return out


def check_vitest_root(d, tid, workspace) -> list[str]:
    """`testroot:` error for one task: a `Test:` command with a runner of cfg `test_roots` (vitest) gives a path that starts with that runner's root; the error names the path without the root prefix."""
    m = TEST_LINE_RE.search(read_text(os.path.join(d, tid + ".md")))
    if not m:
        return []
    entries = _root_entries(project(workspace))
    if not entries:
        return []
    for part in split_commands(m[1].replace("`", " "), amp=False, newline=False):
        words = part.split()
        for runner, root in entries:
            if runner not in words:
                continue
            for word in words:
                path = word[2:] if word.startswith("./") else word
                if path.startswith(root + "/"):
                    fixed = path[len(root) + 1 :]
                    return [
                        f"{TEST_ROOT_PREFIX} {tid}: Test runs `{part.strip()}` with path `{word}`, which repeats the `{runner}` root `{root}` (the runner runs inside it): use `{fixed}`"
                    ]
    return []
