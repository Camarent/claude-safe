"""plan_check rule: a `Test:` line that matches a project `test_wrap` rule but lacks the wrapper text the rule requires."""

import os
import re

from .configuration import TEST_WRAP_KEY, TEST_WRAP_PREFIX, project
from .plan_testcmd import TEST_LINE_RE
from .util import read_text


def _test_wraps(cfg):
    """-> [(compiled match, require text, use text)] from cfg `test_wrap`; a malformed entry (not a list, not a dict, no text fields, a bad regex) is ignored."""
    raw = cfg.get(TEST_WRAP_KEY)
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("match", "require", "use")):
            continue
        try:
            out.append((re.compile(item["match"]), item["require"], item["use"]))
        except re.error:
            continue
    return out


def check_test_wrap(d, tid, workspace) -> list[str]:
    """`testwrap:` error for one task: a `Test:` line matches a `test_wrap` rule of the project and does not contain the rule's `require` text; the error names the `use` wrapper."""
    m = TEST_LINE_RE.search(read_text(os.path.join(d, tid + ".md")))
    if not m:
        return []
    line = m[1].replace("`", " ")
    for pat, require, use in _test_wraps(project(workspace)):
        if pat.search(line) and require not in line:
            return [f"{TEST_WRAP_PREFIX} {tid}: Test runs `{m[1].strip()}` without `{require}` (pipeline.json `test_wrap`): run it through {use}"]
    return []
