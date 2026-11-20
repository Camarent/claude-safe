"""Run: the typed, frozen state of one pipeline run (project dir, stage, limits, dry-run stubs), built once from the parsed CLI args.
The old `argparse.Namespace` attribute names are kept (dir, stage, feature, dry_run, max_*), so call sites read the same."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from .configuration import DEV


@dataclass(frozen=True)
class Run:
    dir: str = ""
    stage: str = ""
    feature: str = ""
    dry_run: bool = False
    allow_dirty: bool = False
    max_waves: int | None = None
    max_dispatches: int | None = None
    max_sessions: int | None = None
    max_budget_usd: float | None = None
    stub: tuple[str, ...] = ()
    stub_plan: dict[tuple[str, str], list[str]] = field(default_factory=dict, compare=False)  # --stub parsed once: {(task, role): [verdicts]}
    orch_events: list[dict[str, Any]] = field(default_factory=list, compare=False)  # decide(): the whole log, for counts that history() resets
    wt_memo: dict[Any, Any] = field(default_factory=dict, compare=False)  # decide(): a per-call cache of git answers (the log is the cache key)

    @classmethod
    def from_args(cls, a: Any) -> Run:
        """Copy the fields an args object (Namespace or any object with .dir/.stage) carries; the others keep their defaults."""
        given = {f.name: getattr(a, f.name) for f in dataclasses.fields(cls) if hasattr(a, f.name) and getattr(a, f.name) is not None}
        stub = tuple(given.get("stub") or ())
        given["stub"] = stub
        if "stub_plan" not in given:
            given["stub_plan"] = _parse_stub(stub)
        return cls(**given)

    @classmethod
    def of(cls, a: Any) -> Run:
        """A Run stays as it is; anything else is converted."""
        return a if isinstance(a, cls) else cls.from_args(a)

    def at(self, **changes: Any) -> Run:
        """A copy with some fields changed (another stage, a scratch dir)."""
        return dataclasses.replace(self, **changes)


def _parse_stub(items: tuple[str, ...]) -> dict[tuple[str, str], list[str]]:
    """--stub TASK:ROLE=V1[,V2...] (repeatable) -> {(task, role): [verdicts]}."""
    plan: dict[tuple[str, str], list[str]] = {}
    for item in items:
        who, _, vs = item.partition("=")
        task, _, role = who.partition(":")
        plan[(task, role or DEV)] = [v.strip().upper() for v in vs.split(",") if v.strip()]
    return plan
