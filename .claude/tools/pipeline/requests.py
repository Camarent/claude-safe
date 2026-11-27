"""Extra agents requested by file (.work/<stage>/requests/<name>.json): validation and the dispatch it becomes."""

import json
import os
import re
from pathlib import Path

from .agents import agent_types
from .configuration import DEFAULT_GATED, DEFAULT_MAX_OPUS, ENV_GATED, ENV_MAX_OPUS, EV_DISPATCH, MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET, TASKS_FILE, project
from .limits import heavy_count, stage_limit
from .util import append_event, read_jsonl

MODELS = {MODEL_HAIKU, MODEL_SONNET, MODEL_OPUS}


def gated() -> set[str]:
    return {x for x in os.environ.get(ENV_GATED, DEFAULT_GATED).split(",") if x}


def _invalid_reason(a, w, r, types):
    """Why a request file is not acceptable (unknown agent, bad model, bad brief); None when it is."""
    if not isinstance(r, dict):
        return "request must be a JSON object"
    agent, model, brief = r.get("agent"), r.get("model"), r.get("brief", "")
    if not isinstance(agent, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", agent) or (types is not None and agent not in types):
        return f"unknown agent {agent!r}"
    if model not in (None, *MODELS):
        return f"bad model {model!r}"
    if not isinstance(brief, str) or not brief:
        return "brief must be an existing file inside .work/<stage>/"
    wr = os.path.realpath(w)
    bp = os.path.realpath(os.path.join(a.dir, brief))
    if not (bp == wr or bp.startswith(wr + os.sep)) or not os.path.isfile(bp):
        return "brief must be an existing file inside .work/<stage>/"
    return None


def _request_action(a, log, name, r, types, n, limit, opus_used, ndisp):
    """The action a valid request becomes; None (after logging a reject) when the opus budget is used up."""
    agent, model, brief = r["agent"], r.get("model"), r["brief"]
    if n >= limit:
        return {"action": "ask_main", "message": f"Sonnet/Opus dispatch budget reached ({n} of {limit}); request {name} not run"}
    eff = model or (types or {}).get(agent, {}).get("model")
    if eff == MODEL_OPUS and opus_used >= int(os.environ.get(ENV_MAX_OPUS, DEFAULT_MAX_OPUS)):
        append_event(log, {"ev": "reject", "request": name, "why": "opus budget used up"})
        return None
    if agent in gated():
        return {
            "action": "ask_main",
            "message": f"lead asks to run '{agent}' (outward-facing); approve by re-running with ORCH_GATED unset for it. brief: {brief}",
        }
    rid = f"a{ndisp + 1}"  # ids are numbered from ALL dispatches (graph.py), not the heavy ones
    bp = os.path.realpath(os.path.join(a.dir, brief))
    why = f"requested by file {name}: {brief}"
    if eff == MODEL_OPUS:  # Opus only behind a logged route (routing_log.unlogged_routes): the lead's reason, or none given
        why = f"route: lead request {r.get('reason') or 'no reason given'}; {why}"
    return {
        "action": "dispatch",
        "id": rid,
        "agent": agent,
        "model": model,
        "task": None,
        "role": "other",
        "why": why,
        "prompt": f"Read {os.path.relpath(bp, a.dir)} and do what it says. Write your full report to .work/{a.stage}/reports/report-{rid}.md, then reply with exactly: REPORT {rid}",
    }


def _load_request(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except ValueError:
        return {}


def take_request(a, w, log) -> dict | None:
    rd = os.path.join(w, "requests")
    if not os.path.isdir(rd):
        return None
    types = agent_types()
    evs = read_jsonl(log)
    n = heavy_count(evs)  # Sonnet/Opus dispatches only: Haiku and scripts do not use up the lead's budget
    limit = stage_limit(os.path.join(w, TASKS_FILE), project(a.dir))
    opus_used = sum(1 for e in evs if e["ev"] == EV_DISPATCH and e.get("model") == MODEL_OPUS)
    ndisp = sum(1 for e in evs if e["ev"] == EV_DISPATCH)
    rejected = {e.get("request"): e.get("mtime") for e in evs if e["ev"] == "reject"}  # name -> mtime of the file when it was rejected
    for name in sorted(os.listdir(rd)):
        p = os.path.join(rd, name)
        if not name.endswith(".json") or not os.path.isfile(p):
            continue
        mtime = os.path.getmtime(p)
        if rejected.get(name) == mtime:  # rejected before and not edited since: no re-parse
            continue
        r = _load_request(p)
        why = _invalid_reason(a, w, r, types)
        if why:  # a bad request stays where it is (only an accepted one moves to done/); the reject is logged once per edit
            append_event(log, {"ev": "reject", "request": name, "why": why, "mtime": mtime})
            continue
        os.makedirs(os.path.join(rd, "done"), exist_ok=True)
        os.replace(p, os.path.join(rd, "done", name))
        action = _request_action(a, log, name, r, types, n, limit, opus_used, ndisp)
        if action:
            return action
    return None
