"""Plan review: a headless plan-reviewer reads the plan, writes a report with a VERDICT line and GAP lines; one architect fix round on FAIL.

The core is pure: `run_review` takes `dispatch(model, kind) -> report text` and `fix(report text)` as callables, so the session wiring lives with the caller."""

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .configuration import (
    PLAN_EXTRA_TASKS_ALLOWED,
    PLAN_FILE,
    PLAN_REVIEW_MODEL_HEAVY,
    PLAN_REVIEW_MODEL_LIGHT,
    PLAN_REVIEW_ROUNDS,
    PLAN_REVIEW_SMALL_MAX_TASKS,
    REQUEST_FILE,
    TASKS_FILE,
    WORK_DIR,
)

logger = logging.getLogger(__name__)

KIND_PHASE, KIND_TASK, KIND_COVER, KIND_SINGLE = "phase", "task", "cover", "single"
REVIEW_KINDS = (KIND_PHASE, KIND_TASK, KIND_COVER, KIND_SINGLE)

VERDICT_DONE, VERDICT_FAIL, VERDICT_BLOCKED = "DONE", "FAIL", "BLOCKED"
VERDICT_EMPTY, VERDICT_ERROR = "", "ERROR"

_VERDICT_RE = re.compile(r"^VERDICT:\s*(DONE|FAIL|BLOCKED)\b", re.M)
_GAP_TASKS_RE = re.compile(r"^GAP-TASKS:\s*(\d+)", re.M)
_GAP_RE = re.compile(r"^GAP:.*$", re.M)
# kinds that review task files: each task's `Affected tests:` must name every importer of its predicted paths
_AFFECTED_KINDS = (KIND_TASK, KIND_SINGLE)
_AFFECTED_CHECK = (
    "For each task, its `Affected tests:` line must name every test file that imports a module of its predicted paths "
    "(run `python3 -I .claude/tools/pipeline/affected.py <predicted paths>` and compare); a missing importer is a GAP line."
)

# what each kind reviews; one entry per kind (adding a kind is one line here plus its model rule)
_KIND_SCOPE = {
    KIND_PHASE: "the phase plan: its phases, goals, done-when checks and dependencies",
    KIND_TASK: "the task plan of one phase: each task's paths, test command, done text and pattern",
    KIND_COVER: "the last phase: whether the plan covers every requirement of the request; list missing tasks as GAP lines",
    KIND_SINGLE: "the whole one-phase plan: phases, tasks, paths, test commands and done texts",
}


@dataclass
class ReviewResult:
    verdict: str
    gaps: list[str] = field(default_factory=list)
    model: str = ""
    rounds: int = 0


def parse_verdict(text: str | None) -> str:
    """-> DONE | FAIL | BLOCKED from the last `VERDICT:` line that starts a line; '' when there is none."""
    found = _VERDICT_RE.findall(text or "")
    return found[-1] if found else VERDICT_EMPTY


def parse_gaps(text: str | None) -> list[str]:
    """-> the `GAP:` lines of the report, in order."""
    return _GAP_RE.findall(text or "")


def parse_gap_tasks(text: str | None) -> int:
    """-> n from the `GAP-TASKS: n` line; without it, the count of `GAP:` lines."""
    m = _GAP_TASKS_RE.findall(text or "")
    return int(m[-1]) if m else len(parse_gaps(text))


def review_model(kind: str, n_tasks: int = 0) -> str:
    """-> model for one review: cover is heavy, phase and task light, single by its task count."""
    if kind in (KIND_PHASE, KIND_TASK):
        return PLAN_REVIEW_MODEL_LIGHT
    if kind == KIND_COVER:
        return PLAN_REVIEW_MODEL_HEAVY
    if kind == KIND_SINGLE:
        return PLAN_REVIEW_MODEL_LIGHT if n_tasks <= PLAN_REVIEW_SMALL_MAX_TASKS else PLAN_REVIEW_MODEL_HEAVY
    raise ValueError(f"unknown review kind: {kind!r}")


def cover_blocked(current_tasks: int, gap_tasks: int, limit: int, extra: int = PLAN_EXTRA_TASKS_ALLOWED) -> bool:
    """True when the plan plus the tasks the cover review adds goes over limit + extra (equal is allowed)."""
    return current_tasks + gap_tasks > limit + extra


def build_prompt(kind: str, feature: str, report_path: str) -> str:
    """Prompt for the plan-reviewer: reads the request, Plan.md and TASKS.md of the feature, writes the report to report_path."""
    if kind not in _KIND_SCOPE:
        raise ValueError(f"unknown review kind: {kind!r}")
    folder = os.path.join(WORK_DIR, feature)
    gap_tasks = " Also write the line `GAP-TASKS: n` (n = tasks the gaps need)." if kind == KIND_COVER else ""
    affected = f" {_AFFECTED_CHECK}" if kind in _AFFECTED_KINDS else ""
    return (
        f"Review the plan of feature {feature}. Scope: {_KIND_SCOPE[kind]}.\n"
        f"Read {os.path.join(folder, REQUEST_FILE)}, {os.path.join(folder, PLAN_FILE)} and {os.path.join(folder, TASKS_FILE)}.\n"
        f"Write your report to {report_path}. For each problem write one line `GAP: <what is missing or wrong, and where>`.{gap_tasks}{affected}\n"
        "End with exactly one line `VERDICT: DONE` (no gaps), `VERDICT: FAIL` (gaps the architect can fix) or `VERDICT: BLOCKED` (the request cannot be planned as written)."
    )


def run_review(
    kind: str,
    dispatch: Callable[[str, str], str],
    fix: Callable[[str], object],
    n_tasks: int = 0,
    limit: int = 0,
) -> ReviewResult:
    """One review. dispatch(model, kind) -> report text; FAIL -> fix(report) up to PLAN_REVIEW_ROUNDS times, no re-review.

    Never raises on a bad report: an empty or garbled verdict returns '' with no fix; a dispatch or fix exception returns ERROR."""
    model = review_model(kind, n_tasks)
    try:
        report = dispatch(model, kind)
    except Exception:
        logger.exception("plan review %s: dispatch failed", kind)
        return ReviewResult(VERDICT_ERROR, model=model)
    verdict = parse_verdict(report)
    gaps = parse_gaps(report)
    if kind == KIND_COVER and (verdict == VERDICT_BLOCKED or cover_blocked(n_tasks, parse_gap_tasks(report), limit)):
        logger.warning("plan review cover: blocked (verdict %s, %d gap tasks)", verdict or "none", parse_gap_tasks(report))
        return ReviewResult(VERDICT_BLOCKED, gaps, model)
    if verdict in (VERDICT_EMPTY, VERDICT_DONE, VERDICT_BLOCKED):
        if verdict == VERDICT_EMPTY:
            logger.warning("plan review %s: no VERDICT line in report", kind)
        return ReviewResult(verdict, gaps, model)
    rounds = 0
    for _ in range(PLAN_REVIEW_ROUNDS):
        try:
            fix(report)
        except Exception:
            logger.exception("plan review %s: fix round %d failed", kind, rounds + 1)
            return ReviewResult(VERDICT_ERROR, gaps, model, rounds)
        rounds += 1
    if gaps:
        logger.warning("plan review %s: FAIL, %d gap(s) left after %d fix round(s)", kind, len(gaps), rounds)
    return ReviewResult(VERDICT_FAIL, gaps, model, rounds)
