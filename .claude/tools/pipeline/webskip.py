"""webskip: the visible record of a browser check that could not run because headless sessions have no browser tools."""

import glob
import os
from typing import Any

from .configuration import WEBTEST_SKIP_LINE, WEBTEST_SKIP_MARK, WEBTEST_STEP, webtest_tools_missing
from .stagedir import resolve_stage
from .util import read_text


def line(cfg: dict[str, Any], ids: str) -> str:
    """-> the `UNVERIFIED-WEB: skipped: no browser tools ...` line for the tasks IDS (a string)."""
    return WEBTEST_SKIP_LINE.format(tools=", ".join(webtest_tools_missing(cfg)), ids=ids)


def skips(d: str, stage: str) -> list[str]:
    """-> the distinct UNVERIFIED-WEB lines of the browser-check reports of the stage (project D)."""
    root = resolve_stage(d, stage)
    found: list[str] = []
    for f in sorted(glob.glob(os.path.join(root, "reports", f"report-{WEBTEST_STEP}*.md")) + glob.glob(os.path.join(root, f"report-{WEBTEST_STEP}*.md"))):
        found += [x.strip() for x in read_text(f).splitlines() if x.startswith(WEBTEST_SKIP_MARK)]
    return list(dict.fromkeys(found))
