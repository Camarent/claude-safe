"""The tool-block rule (an error): a task whose Test or Done runs a JS tool (madge, knip, eslint) whose smoke probe fails is blocked at plan time, unless the task depends on a manual `ops-N` row (the tool is fixed by hand there). A Test or Done that runs `knip --production` while the knip config has no entry key is `knip-prod:`. No JS project (no package.json) or an absent tool gives no problem: a Python-only stage is never blocked."""

import os
import re

from . import tool_probe
from .configuration import KNIP_PROD_CMD, KNIP_PROD_PREFIX, OPS_DEP_PREFIX, TOOLBLOCK_PREFIX, TOOLBLOCK_TOOLS
from .extra_tools import JS
from .plan_check_task import _repo_root
from .task_text import lines
from .util import read_text

# a tool name as a word: not inside a longer word, not a file name (`madge.py`, `knip-entry`)
TOOL_RE = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(t) for t in TOOLBLOCK_TOOLS) + r")(?![\w-]|\.\w)")
PKG_JSON = "package.json"


def _text(d, tid) -> str:
    return read_text(os.path.join(d, tid + ".md"))


def _installed(tool) -> bool:
    """True when the tool binary is in the JS tool dir (absent tools are never blocked)."""
    return os.path.isfile(os.path.join(JS, "node_modules", ".bin", tool))


def _tools(body) -> list[str]:
    """-> the distinct JS tool names of BODY, in order of first use."""
    out: list[str] = []
    for m in TOOL_RE.finditer(body):
        if m[1] not in out:
            out.append(m[1])
    return out


def _entry_problems(e, body, proj, probe, installed) -> list[str]:
    """Problems of one entry E whose Test/Done text is BODY: the probe of each tool it names, and the knip-prod check."""
    probs = []
    if not any(dep.startswith(OPS_DEP_PREFIX) for dep in e["depends"]):
        for tool in _tools(body):
            if not installed(tool):
                continue
            got = probe(tool, proj)
            if not got.ok:
                probs.append(f"{TOOLBLOCK_PREFIX} {e['id']} uses {tool}: {got.reason}")
    if KNIP_PROD_CMD in body and installed("knip"):
        reason = tool_probe.knip_entry_problem(proj)
        if reason:
            probs.append(f"{KNIP_PROD_PREFIX} {e['id']} runs {KNIP_PROD_CMD}: {reason}")
    return probs


def check_toolblock(d, ents, probe=tool_probe.probe, installed=_installed) -> list[str]:
    """`tool-block:` and `knip-prod:` errors for the entries of ENTS whose Test/Done names a JS tool. No package.json in the repo: no check."""
    proj = _repo_root(d)
    if not os.path.isfile(os.path.join(proj, PKG_JSON)):
        return []
    probs = []
    for e in ents:
        body = " ".join(value for _, value in lines(_text(d, e["id"]), "Test", "Done"))
        probs += _entry_problems(e, body, proj, probe, installed)
    return probs
