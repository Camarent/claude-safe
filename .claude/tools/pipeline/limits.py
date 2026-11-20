"""The Sonnet/Opus dispatch limit of a stage: a fixed part for the known heavy steps + a multiplier per task for escalation. Recomputed from TASKS.md before each batch, so fix tasks add their share."""

import os
import sys

from .agents import agent_types
from .configuration import ENV_MAX_DISPATCH, EV_DISPATCH, MODEL_OPUS, MODEL_SONNET
from .tasks import parse_flags, parse_tasks

# fixed part: heavy sessions every stage may use besides its tasks (each is one Sonnet or Opus session)
AUDITS = 2  # sec-auditor (Sonnet, Opus rerun after ESCALATE) + arch-auditor (Sonnet)
UX_AUDIT = 1  # ux-reviewer (Sonnet), UI stages
FIX_ARCHITECT = 1  # one architect session per fix round: after a red final-test, after a red web-test (UI), after audits that said FIXES
REAUDIT = 2  # sec + arch run once more after the fixes (Haiku when the fixes are small: this is the ceiling)
# per task: the escalation allowance
# This limit is a safety net against runaway loops, not a budget: going back to the lead must stay rare, so the multipliers are the real ceilings of a task.
PER_SONNET_TASK = 4  # Sonnet owner: dev + reviewer, one rework round (dev + reviewer), then the lead
PER_HAIKU_TASK = 2  # a Haiku task that escalates: Sonnet dev + Sonnet reviewer (healthy stages use well under 1 on average)


def fixed_part(flags):
    n = 0
    if flags["audit"]:
        n += AUDITS + FIX_ARCHITECT + REAUDIT + (UX_AUDIT if flags["ui"] else 0)
    if flags["test"]:
        n += FIX_ARCHITECT
    if flags["ui"] and flags["test"]:
        n += FIX_ARCHITECT  # the web-test fix round
    return n


def heavy_limit(tasks_md, cfg=None):
    """-> (limit, fixed part, task part). `cfg` (pipeline.json) may set heavy_per_sonnet_task / heavy_per_haiku_task."""
    cfg = cfg or {}
    flags, tasks = parse_flags(tasks_md), parse_tasks(tasks_md)
    s_mult = cfg.get("heavy_per_sonnet_task", PER_SONNET_TASK)
    h_mult = cfg.get("heavy_per_haiku_task", PER_HAIKU_TASK)
    per_task = sum(s_mult if (t["owner_sonnet"] or t["review_sonnet"]) else h_mult for t in tasks)
    fixed = fixed_part(flags)
    return round(fixed + per_task), fixed, round(per_task)


def is_heavy(agent, model):
    """True for a Sonnet or Opus session: the model named in the dispatch, else the agent file's default. Scripts are never heavy."""
    if agent == "script":
        return False
    return (model or ((agent_types() or {}).get(agent) or {}).get("model")) in (MODEL_SONNET, MODEL_OPUS)


def heavy_count(evs):
    """Sonnet/Opus dispatches so far in an orch.jsonl event list."""
    return sum(1 for e in evs if e["ev"] == EV_DISPATCH and is_heavy(e.get("agent", ""), e.get("model")))


def stage_limit(tasks_md, cfg=None):
    """The one Sonnet/Opus limit of a stage: ORCH_MAX_DISPATCH when set, else the task-based formula."""
    env = os.environ.get(ENV_MAX_DISPATCH)
    if env:
        try:
            return int(env)
        except ValueError:
            sys.stderr.write(f"warning: {ENV_MAX_DISPATCH}={env!r} is not an integer, using the task-based limit\n")
    return heavy_limit(tasks_md, cfg)[0]


def session_guard(tasks_md):
    """Loop guard over ALL model sessions: 60 + 8 per task (a task never needs more than ~9)."""
    return 60 + 8 * len(parse_tasks(tasks_md))
