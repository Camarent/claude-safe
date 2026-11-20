"""Trigger log (every Opus-class or high-effort call, with its outcome) and the retro signals."""

import os
import re
import time

from .configuration import (
    DONE,
    ESCALATE,
    EV_DISPATCH,
    EV_REPORT,
    FAIL,
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    TASKS_FILE,
    TRIGGER_ESCALATION_PCT,
    TRIGGER_FIX_SHARE_PCT,
    WORK_DIR,
    project,
)
from .limits import heavy_count, stage_limit
from .steps import history
from .tasks import parse_tasks
from .util import append_jsonl, paths, read_jsonl, work_path
from .verdicts import AUDITORS

TRIGGER_AGENTS = AUDITORS


def is_trigger(d):
    return (
        d["agent"] in TRIGGER_AGENTS
        or (d["agent"] == "architect" and d.get("model") == MODEL_OPUS)
        or d.get("model") == MODEL_OPUS
        or d.get("effort") == "high"
    )


def trigger_log(a):
    return os.path.join(a.dir, WORK_DIR, "triggers.jsonl")


def log_trigger(a, rec):
    append_jsonl(trigger_log(a), {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": a.stage, **rec})


def _high_effort_signals(a):
    high = [r for r in read_jsonl(trigger_log(a)) if r["ev"] == "trigger" and r["stage"] == a.stage and r.get("effort") == "high"]
    if not high:
        return []
    return [("high-effort", f"{len(high)} call(s) at effort high: " + ", ".join(f"{r['id']} {r['agent']}" for r in high))]


def _fix_ids(evs, tasks):
    """Ids of the dev tasks dispatched after the fix-plan report."""
    fp = next((i for i, e in enumerate(evs) if e["ev"] == EV_REPORT and str(e.get("task", "")).startswith("fix-plan")), None)
    if fp is None:
        return set()
    return {e["task"] for e in evs[fp:] if e["ev"] == EV_DISPATCH and e.get("role") == "dev" and e.get("task") in {t["id"] for t in tasks}}


def _review_round_signals(evs, tasks):
    rounds = {t["id"]: sum(1 for e in history(evs, t["id"]) if e["ev"] == EV_REPORT and e["role"] == "reviewer" and e["verdict"] == FAIL) for t in tasks}
    if any(v >= 2 for v in rounds.values()):
        return [("review-rounds", "tasks with 2+ failed review rounds: " + ", ".join(k for k, v in rounds.items() if v >= 2))]
    return []


def _escalation_signals(evs, tasks):
    esc = {t["id"] for t in tasks if any(e["ev"] == EV_REPORT and e["verdict"] == ESCALATE for e in history(evs, t["id"]))}
    if tasks and len(esc) / len(tasks) > TRIGGER_ESCALATION_PCT / 100:
        return [("escalations", f"{len(esc)} of {len(tasks)} tasks escalated to Sonnet (>{TRIGGER_ESCALATION_PCT}%)")]
    return []


def _dev_report_chains(evs):
    """Per task: ordered (model, verdict) of dev reports."""
    model = {e.get("id"): e.get("model") for e in evs if e["ev"] == EV_DISPATCH and e.get("role") == "dev"}
    chains: dict = {}
    for e in evs:
        if e["ev"] == EV_REPORT and e.get("role") == "dev" and e.get("task"):
            chains.setdefault(e["task"], []).append((model.get(e.get("id")) or "?", e["verdict"]))
    return chains


def _is_useless_chain(ch):
    return any(m == MODEL_HAIKU and v == ESCALATE and any(m2 == MODEL_SONNET and v2 != DONE for m2, v2 in ch[i + 1 :]) for i, (m, v) in enumerate(ch))


def _useless_escalation_signals(evs):
    """A Haiku dev ESCALATE whose Sonnet run did not end DONE (ESCALATE, BLOCKED or FAIL) paid for nothing: the chain per task is listed to investigate."""
    out = [f"{tid}: " + " > ".join(f"{m2} {v2}" for m2, v2 in ch) for tid, ch in _dev_report_chains(evs).items() if _is_useless_chain(ch)]
    return [("useless-escalations", "Haiku ESCALATE then Sonnet not DONE, investigate: " + "; ".join(out))] if out else []


def _audit_fix_signals(evs):
    deep = [e["task"] for e in evs if e["ev"] == EV_REPORT and e.get("task") in ("audit-sec", "audit-arch") and e["verdict"] == "FIXES"]
    return [("audit-fixes", "sec/arch audits asked for fixes: " + ", ".join(deep))] if deep else []


def _fix_share_signals(evs, tasks):
    fix_ids = _fix_ids(evs, tasks)
    base = [t for t in tasks if t["id"] not in fix_ids]
    if base and len(fix_ids) / len(base) > TRIGGER_FIX_SHARE_PCT / 100:
        return [("fix-share", f"{len(fix_ids)} fix task(s) vs {len(base)} base task(s) (>{TRIGGER_FIX_SHARE_PCT}%)")]
    return []


def _stuck_signals(evs):
    stuck = sum(1 for e in evs if e["ev"] == "ask")
    manual = sum(1 for e in evs if e["ev"] in ("retry", "skip") and e.get("task") != "master-merged" and not e.get("auto"))
    if stuck >= 3 or manual:
        return [("stuck", f"{stuck} ask_main stop(s), {manual} retry/skip")]
    return []


def _dispatch_signals(a, w, evs):
    n, lim = heavy_count(evs), stage_limit(os.path.join(w, TASKS_FILE), project(a.dir))
    if n > int(lim * 0.75):
        return [("dispatches", f"{n} Sonnet/Opus dispatches (limit {lim})")]
    return []


def _number_signals(w):
    rn = work_path(w, "retro-numbers.md")  # context / turns / cost signals computed from the transcripts by the final Haiku step
    sig = []
    if os.path.exists(rn):
        with open(rn, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"RETRO ([a-z-]+): (.*)", line.strip())
                if m:
                    sig.append((m.group(1), m.group(2)))
    return sig


def retro_signals(a):
    """-> [(name, text)] for every retro signal that tripped, from orch.jsonl and triggers.jsonl. Context, turns and cost come from ledger.py (see cmd_retro)."""
    w, log = paths(a)
    evs = read_jsonl(log)
    tasks = parse_tasks(os.path.join(w, TASKS_FILE))
    return (
        _high_effort_signals(a)
        + _review_round_signals(evs, tasks)
        + _escalation_signals(evs, tasks)
        + _useless_escalation_signals(evs)
        + _audit_fix_signals(evs)
        + _fix_share_signals(evs, tasks)
        + _stuck_signals(evs)
        + _dispatch_signals(a, w, evs)
        + _number_signals(w)
    )
