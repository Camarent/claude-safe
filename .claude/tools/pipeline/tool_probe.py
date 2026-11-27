"""Smoke probe for the JS tools (madge, knip, eslint): runs `<tool> --version`, checks the peer range against the project's typescript, and checks the knip entry config.

probe(tool, proj) -> Probe(ok, reason)     reason is "" when ok
knip_entry_problem(proj) -> reason or ""   "" when the knip config has an entry key
"""

import json
import os
import re
from typing import NamedTuple

from . import extra_tools

PEER_PKG = "typescript"
VERSION_TIMEOUT_S = 60
TOKEN = re.compile(r"(>=|<=|>|<|\^|~|=)?v?(\d+)[.\w*-]*")


class Probe(NamedTuple):
    ok: bool
    reason: str


def _json(path) -> dict:
    """Parsed JSON object at `path`; {} when the file is missing or not an object."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def majors(rng) -> set[int] | None:
    """Major versions a semver range admits: '^5' -> {5}, '>=4 <6' -> {4, 5}, '5 || 6' -> {5, 6}. None when unparsed."""
    out: set[int] = set()
    for alt in rng.split("||"):
        lo = hi = exact = None
        for tok in alt.split():
            m = TOKEN.fullmatch(tok)
            if not m:
                return None
            op, n = m.group(1), int(m.group(2))
            if op == ">=":
                lo = n
            elif op == ">":
                lo = n + 1
            elif op == "<":
                hi = n
            elif op == "<=":
                hi = n + 1
            else:
                exact = n
        if exact is not None and lo is None and hi is None:
            out.add(exact)
        elif lo is not None and hi is not None:
            out.update(range(lo, hi))
        else:
            return None  # open-ended or empty comparator: not a bounded major set
    return out or None


def _project_range(proj) -> str:
    pkg = _json(os.path.join(proj, "package.json"))
    for key in ("dependencies", "devDependencies"):
        rng = (pkg.get(key) or {}).get(PEER_PKG)
        if rng:
            return rng
    return ""


def _peer_problem(tool, proj, js) -> str:
    """Reason when the tool's typescript peer range excludes the project's typescript major; '' otherwise."""
    peer = (_json(os.path.join(js, "node_modules", tool, "package.json")).get("peerDependencies") or {}).get(PEER_PKG)
    proj_rng = _project_range(proj)
    if not peer or not proj_rng:
        return ""
    peer_set, proj_set = majors(peer), majors(proj_rng)
    if peer_set is None or proj_set is None or proj_set <= peer_set:
        return ""
    fmt = "/".join(str(n) for n in sorted(peer_set))
    fmt_proj = "/".join(str(n) for n in sorted(proj_set))
    return f"{tool} peer {PEER_PKG} {fmt} vs project {fmt_proj}"


def probe(tool, proj, run=extra_tools.run, js=extra_tools.JS) -> Probe:
    """Version smoke run, then the peer range check. A non-zero exit returns the tool output as reason."""
    rc, out = run([f"{js}/node_modules/.bin/{tool}", "--version"], VERSION_TIMEOUT_S)
    if rc != 0:
        return Probe(False, out)
    problem = _peer_problem(tool, proj, js)
    return Probe(not problem, problem)


def knip_entry_problem(proj) -> str:
    """Reason when knip has no config with an entry key (knip.json, else package.json knip key); '' otherwise."""
    path = os.path.join(proj, "knip.json")
    if os.path.isfile(path):
        cfg = _json(path)
    else:
        pkg_knip = _json(os.path.join(proj, "package.json")).get("knip")
        if not isinstance(pkg_knip, dict):
            return "knip config missing: knip.json or package.json knip key"
        cfg = pkg_knip
    if "entry" not in cfg:
        return "knip config has no entry key"
    return ""
