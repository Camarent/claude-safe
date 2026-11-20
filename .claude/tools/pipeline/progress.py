"""One short status line per batch of sessions, printed by `wave.py run`/`feature`: where we are without searching the logs."""

import collections
import os

from .configuration import EV_REPORT, EV_SKIP, PLAN_FILE, TASKS_FILE
from .plan_files import parse_phase_rows
from .steps import history
from .tasks import parse_tasks
from .util import read_jsonl, read_text


def position(tasks_md):
    """`p2/3 02-api` for a stage folder `.work/<feature>/phases/NN-name`, else the folder name."""
    folder = os.path.dirname(os.path.abspath(tasks_md))
    parent = os.path.dirname(folder)
    if os.path.basename(parent) == "phases":
        names = [p["dir"] for p in parse_phase_rows(read_text(os.path.join(os.path.dirname(parent), PLAN_FILE)))]
        if os.path.basename(folder) in names:
            return f"p{names.index(os.path.basename(folder)) + 1}/{len(names)} {os.path.basename(folder)}"
    return os.path.basename(folder)


def phase_line(folder, text):
    """`[p2/5 02-api] text`: a status line for a phase folder `.work/<feature>/phases/NN-name`, in the form of status_line (planning steps use it)."""
    return f"[{position(os.path.join(folder, TASKS_FILE))}] {text}"


def _where(of, tasks, ds):
    """`wave 1/2` for dispatches inside the task waves, else the step ids."""
    waves = sorted({tasks[of[d["id"]]]["wave"] for d in ds if of.get(d["id"]) in tasks})
    if waves:
        return f"wave {', '.join(map(str, waves))}/{max(t['wave'] for t in tasks.values())}"
    return ", ".join(dict.fromkeys(of.get(d["id"], d["id"]) for d in ds))


def status_line(c, ds):
    """`[p2/3 02-api] wave 1/2 · tasks 2/5 done · dev×2 reviewer · 14 model calls · $0.42` (steps outside the task waves show their id)."""
    tm = os.path.join(c.w, TASKS_FILE)
    tasks = {t["id"]: t for t in parse_tasks(tm)}
    evs = read_jsonl(c.log)
    done = sum(
        1
        for tid in tasks
        if any(e["ev"] == EV_SKIP or (e["ev"] == EV_REPORT and e["role"] == "reviewer" and e["verdict"] == "PASS") for e in history(evs, tid))
    )
    where = _where(c.tasks(), tasks, ds)
    who = " ".join(f"{k}×{n}" if n > 1 else k for k, n in collections.Counter(d["agent"] for d in ds).items())
    return (
        f"[{position(tm)}] {where} · tasks {done}/{len(tasks)} done · {who} · {c.disp}{'/' + str(c.limit) if c.limit else ''} Sonnet/Opus calls · ${c.cost:.2f}"
    )


def planning_lines(c):
    """One line per planning step of a feature from its metrics.jsonl (`plan-step` events): context, brief, architect, check; a started step with no done line shows as running."""
    last = {}
    for e in read_jsonl(c.metrics):
        if e.get("ev") == "plan-step":
            last[e["step"]] = e
    out = []
    for name, e in last.items():
        extra = " ".join(f"{k}={e[k]}" for k in ("model", "verdict", "secs", "local_model_secs", "code_files", "tasks", "errors", "usd") if k in e)
        out.append(f"   plan/{name}: {'running' if e['state'] == 'start' else 'done'}" + (f" ({extra})" if extra else ""))
    return out
