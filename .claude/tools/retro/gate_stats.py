"""Retro gate stats: the gate_cmd records of a feature grouped by gate time level (unit, merge, full). Per group: the real runs, the skipped runs, the median and p90 seconds of the real runs and the gate_time_warn_s limit.
Records of old runs without `skipped` count as real runs; a record without `level` takes the level of its `kind`."""

import math
import statistics

from pipeline import gate_timing
from pipeline.configuration import GATE_LEVEL_FULL, GATE_LEVEL_MERGE, GATE_LEVEL_UNIT, GATE_STATS_P90

GROUPS = (GATE_LEVEL_UNIT, GATE_LEVEL_MERGE, GATE_LEVEL_FULL)  # output order of the groups


def _group(rec):
    """-> the gate level (group) of a gate_cmd record, None when it has no known level."""
    return rec.get("level") or gate_timing.LEVEL_OF_KIND.get(rec.get("kind"))


def _p90(values):
    """-> the nearest-rank p90 of the sorted VALUES (GATE_STATS_P90)."""
    return values[math.ceil(GATE_STATS_P90 * len(values)) - 1]


def stats(rows, limits):
    """-> {group: {"runs", "skipped", "median", "p90", "limit"}} for every group of GROUPS that has records in ROWS; median and p90 are None when no real run has seconds; LIMITS is gate_time_warn_s."""
    out = {}
    for group in GROUPS:
        recs = [r for r in rows if _group(r) == group]
        if not recs:
            continue
        real = [r for r in recs if r.get("skipped") is not True]
        secs = sorted(float(r["seconds"]) for r in real if isinstance(r.get("seconds"), (int, float)))
        out[group] = {
            "runs": len(real),
            "skipped": len(recs) - len(real),
            "median": statistics.median(secs) if secs else None,
            "p90": _p90(secs) if secs else None,
            "limit": limits.get(group),
        }
    return out


def _secs(value):
    return "-" if value is None else f"{value:.1f}s"


def text(group, st):
    """-> the gate stats line of one group (the output of stats)."""
    limit = "-" if st["limit"] is None else f"{st['limit']}s"
    return f"gate stats {group}: {st['runs']} runs, {st['skipped']} skipped, median {_secs(st['median'])}, p90 {_secs(st['p90'])}, limit {limit}"


def lines(rows, limits):
    """-> one gate stats line per group with records in ROWS."""
    return [text(group, st) for group, st in stats(rows, limits).items()]
