"""Routing log checks over orch.jsonl events: unlogged Sonnet/Opus routes and off-rule E1(a)/E1(b) dispatches.

unlogged_routes(events): every dispatch on Sonnet or Opus whose `why` has no E1(x) letter and no `route:` prefix.
off_rule(events): an E1(a) dev dispatch with fewer than HAIKU_DEV_TRIES earlier Haiku dev dispatches of its task,
  and an E1(b) dev dispatch with fewer than E4_ESCALATES earlier dev ESCALATE reports of its task (E4).
Both checks read the task's own events: the unit is the task id. This module imports nothing from steps.
"""

import re

from .configuration import ESCALATE, EV_DISPATCH, EV_REPORT, HAIKU_DEV_TRIES, MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET

E1_LETTER_RE = re.compile(r"E1\([a-d]\)")
ROUTE_PREFIX = "route:"
UNLOGGED_WHY = "route: unlogged: Sonnet dispatch with no E1(x) letter"  # the why _disp writes when a Sonnet route is unlogged
E4_ESCALATES = 2  # dev ESCALATE reports of one task that keep it on Sonnet (E4)


def _why(e):
    return e.get("why") or ""


def _logged(e):
    """True when the dispatch's `why` names an E1 letter or starts with `route:`."""
    why = _why(e)
    return bool(E1_LETTER_RE.search(why)) or why.startswith(ROUTE_PREFIX)


def unlogged_routes(events):
    """Dispatches on Sonnet or Opus whose `why` carries neither an E1(x) letter nor a `route:` prefix."""
    return [e for e in events if e["ev"] == EV_DISPATCH and e.get("model") in (MODEL_SONNET, MODEL_OPUS) and not _logged(e)]


def _haiku_dev_dispatches_before(earlier, task):
    return sum(1 for e in earlier if e["ev"] == EV_DISPATCH and e.get("role") == "dev" and e.get("task") == task and e.get("model") == MODEL_HAIKU)


def _dev_escalates_before(earlier, task):
    return sum(1 for e in earlier if e["ev"] == EV_REPORT and e.get("role") == "dev" and e.get("task") == task and e.get("verdict") == ESCALATE)


def off_rule(events):
    """E1(a) dev dispatches with fewer than HAIKU_DEV_TRIES earlier Haiku dev dispatches, and E1(b) dev dispatches with fewer than E4_ESCALATES earlier dev ESCALATEs, of their task."""
    flagged = []
    for i, e in enumerate(events):
        if e["ev"] != EV_DISPATCH or e.get("role") != "dev":
            continue
        why = _why(e)
        task = e.get("task")
        short_haiku = why.startswith("E1(a)") and _haiku_dev_dispatches_before(events[:i], task) < HAIKU_DEV_TRIES
        short_escalates = why.startswith("E1(b)") and _dev_escalates_before(events[:i], task) < E4_ESCALATES
        if short_haiku or short_escalates:
            flagged.append(e)
    return flagged
