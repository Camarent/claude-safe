"""The project's named UI flows (pipeline.json `ux_flows`): a JSON list of `{name, actions, covers?}` in the `ux_shots.js --steps` format. `covers` lists globs of the UI source files whose change makes the flow relevant. The file is project content; this module only reads and checks it."""

import fnmatch
import json
import os
import re

from .configuration import UX_FLOW_ACTIONS, ux_flows


def variables(tasks_text, given=()):
    """-> the `UX-VARS: name=value ...` variables of a TASKS.md text, overridden by GIVEN `name=value` strings."""
    v: dict[str, str] = {}
    for m in re.finditer(r"^UX-VARS:\s*(.+)$", tasks_text, re.M):
        for kv in m.group(1).split():
            if "=" in kv:
                key, val = kv.split("=", 1)
                v[key] = val
    for kv in given:
        key, val = kv.split("=", 1)  # ValueError without "=", as before
        v[key] = val
    return v


def sub(s, v):
    """-> S with each `<name>` replaced from V (an unknown name stays)."""
    return re.sub(r"<(\w+)>", lambda m: v.get(m.group(1), m.group(0)), s)


def flows_rel(cfg, v=None):
    """-> the configured flows path (repo-relative) with its `<name>` placeholders filled from V (a per-course path such as `workspace/<course>/ux/flows.json`), '' when none is configured."""
    return sub(ux_flows(cfg), v or {})


def path_problem(rel):
    """-> a problem text when REL (the repo-relative flows path) is absolute or climbs out of the repo, else ''."""
    parts = rel.replace("\\", "/").split("/")
    return f"the path {rel!r} must be relative and stay inside the repo" if rel and (os.path.isabs(rel) or ".." in parts) else ""


def flows_file(repo, cfg, v=None):
    """-> absolute path of the configured flows file under REPO, '' when none is configured. The file may sit in a nested repo below REPO."""
    rel = flows_rel(cfg, v)
    return os.path.join(repo, rel) if rel else ""


def _action_problem(i, a):
    if not isinstance(a, dict) or len(a) != 1 or next(iter(a)) not in UX_FLOW_ACTIONS:
        return f"flow {i}: an action must be an object with exactly one of {', '.join(UX_FLOW_ACTIONS)}"
    return ""


def _flow_problems(i, f):
    if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not f["name"].strip():
        return [f"flow {i}: needs a non-empty `name`"]
    acts, covers = f.get("actions"), f.get("covers", [])
    probs = [] if isinstance(acts, list) else [f"flow {f['name']}: `actions` must be a list"]
    if isinstance(acts, list):
        probs += [p for p in (_action_problem(f["name"], a) for a in acts) if p]
    if not isinstance(covers, list) or not all(isinstance(c, str) for c in covers):
        probs.append(f"flow {f['name']}: `covers` must be a list of glob strings")
    return probs


def read(path):
    """-> (flows, problems) of the flows file PATH: a missing or invalid file gives problems and no flows."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as e:
        return [], [f"{path}: cannot read the flows file ({e.strerror})"]
    except ValueError as e:
        return [], [f"{path}: not valid JSON ({e})"]
    if not isinstance(data, list) or not data:
        return [], [f"{path}: must be a non-empty JSON list of flows"]
    probs = [p for i, f in enumerate(data) for p in _flow_problems(i, f)]
    names = [f.get("name") for f in data if isinstance(f, dict)]
    probs += [f"flow name {n!r} is used twice" for n in sorted({n for n in names if names.count(n) > 1}, key=str)]
    return ([] if probs else data), [f"{path}: {p}" for p in probs]


def covering(flows, changed):
    """-> (names of the flows whose `covers` match a file of CHANGED, the changed files no flow covers)."""
    hit = [f["name"] for f in flows if any(fnmatch.fnmatch(c, g) for g in f.get("covers", []) for c in changed)]
    rest = [c for c in changed if not any(fnmatch.fnmatch(c, g) for f in flows for g in f.get("covers", []))]
    return hit, rest
