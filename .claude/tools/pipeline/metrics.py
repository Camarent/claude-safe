"""metrics.jsonl: one line per session, per wave and one total, plus free-form event lines; per model/agent summary."""

from collections import defaultdict

from .claude import SUMS
from .configuration import BLOCKED, EV_DISPATCH, FAIL
from .util import append_jsonl, now, read_jsonl


def session_line(c, id, agent, model, u, verdict="", task="", reason="") -> dict:
    """Record one session; `u` is a claude.usage() dict; an optional u["tools"] ({name: calls}) is stored as-is.
    A dispatch without a model runs on the agent file's default: that name is recorded, not null."""
    if not model and agent != "script":
        from .requests import agent_types

        model = ((agent_types() or {}).get(agent) or {}).get("model") or model
    rec = {
        "ev": "session",
        "ts": now(),
        "stage": c.stage,
        "wave": c.wave,
        "id": id,
        "task": task,
        "agent": agent,
        "model": model,
        **{k: u[k] for k in SUMS},
        "sid": u.get("sid", ""),
        "verdict": verdict,
        "error": u["error"],
    }
    if u.get("seconds_api"):
        # `seconds` is claude wall time and includes backgrounded waits; `seconds_api` is only API time. Absent in old lines.
        rec["seconds_api"] = u["seconds_api"]
    if u.get("tools"):
        rec["tools"] = u["tools"]  # optional {tool name: calls}; absent in old lines
    if u.get("denials"):
        # commands the headless agent was denied (`Tool: command [reason] guard`, max 5); a BLOCKED report names its cause here.
        # The last word is the guard name (denials.jsonl rule), absent when no logged row matches.
        rec["denied"] = u["denials"]
    if u.get("search"):
        rec["search"] = u["search"]  # optional {local:find|local:nav-def|grep|glob: calls}; share = local / all
    if reason and verdict in (BLOCKED, FAIL):
        rec["reason"] = reason  # why the step did not pass (first line of its report); absent on other verdicts and in old lines
    c.cost += rec["cost_usd"]
    c.sess.append(rec)
    append_jsonl(c.metrics, rec)
    return rec


def event(c, name, **fields) -> None:
    """One free-form stat line (ev = NAME), e.g. the plan-context step; readers that look for ev == "session" skip it."""
    append_jsonl(c.metrics, {"ev": name, "ts": now(), "stage": c.stage, **fields})


def wave_line(c, wall) -> None:
    rec = {
        "ev": "wave",
        "ts": now(),
        "stage": c.stage,
        "wave": c.wave,
        "sessions": len(c.sess),
        **{k: sum(s[k] for s in c.sess) for k in SUMS},
        "seconds_wall": round(wall, 1),
    }
    if any("seconds_api" in s for s in c.sess):
        rec["seconds_api"] = round(sum(s.get("seconds_api", 0) for s in c.sess), 1)  # only when a session carries it
    append_jsonl(c.metrics, rec)
    c.sess = []


def missing_sessions(events, rows) -> list[str]:
    """-> dispatch ids in orch.jsonl `events` with no `session` row in metrics.jsonl `rows`; script-role dispatches run no model and need none."""
    have = {r.get("id") for r in rows if r.get("ev") == "session"}
    return [e["id"] for e in events if e.get("ev") == EV_DISPATCH and e.get("role") != "script" and e["id"] not in have]


def total_line(c) -> None:
    rows = read_jsonl(c.metrics)
    ss = [r for r in rows if r.get("ev") == "session"]
    append_jsonl(c.metrics, {"ev": "total", "ts": now(), "stage": c.stage, "waves": c.wave, "sessions": len(ss), **{k: sum(s[k] for s in ss) for k in SUMS}})
    missing = missing_sessions(read_jsonl(c.log), rows)
    if missing:
        print(f"no session event: {len(missing)} dispatches (cost undercount)")


def aggregate(rows) -> dict:
    """-> {(model, agent): sums + sessions} over the session lines."""
    agg: defaultdict = defaultdict(lambda: dict.fromkeys(SUMS, 0) | {"sessions": 0})
    for r in rows:
        if r.get("ev") == "session":
            g = agg[(r["model"], r["agent"])]
            g["sessions"] += 1
            for k in SUMS:
                g[k] += r[k]
    return dict(agg)


def session_totals(path) -> tuple[int, float]:
    """-> (sessions, USD) over every `session` row of a metrics.jsonl, ops and script steps included."""
    ss = [r for r in read_jsonl(path) if r.get("ev") == "session"]
    return len(ss), sum(r.get("cost_usd") or 0 for r in ss)
