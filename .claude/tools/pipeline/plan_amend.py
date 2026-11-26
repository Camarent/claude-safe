"""`cs-plan amend`: change one not-done phase row of Plan.md (note, goal, done_when, paths, depends). The caller runs the feature check after the write."""

from . import plan_files as pf

AMEND_NOTE_PREFIX = "Amended: "


def amend_phase(a, phases) -> dict:
    """Apply the amend options of `a` to the phase named `a.phase` and rewrite Plan.md. Refuses an unknown phase, a done phase and an unknown depends."""
    p = pf.phase_ref(phases, a.phase)
    if p is None:
        raise pf.PlanError(f"unknown phase {a.phase}")
    if p["status"] == "done":
        raise pf.PlanError(f"phase {p['dir']} is done: amend refused")
    for dep in pf.csv(a.depends):
        if not pf.phase_ref(phases, dep):
            raise pf.PlanError(f"depends on unknown phase {dep}")
    base = a.goal or p["goal"]  # --goal replaces the goal; the note is appended to whichever goal stands
    p["goal"] = f"{base} {AMEND_NOTE_PREFIX}{a.note.strip()}".strip()
    if a.done_when:
        p["done_when"] = a.done_when
    if a.paths:
        p["paths"] = pf.csv(a.paths)
    if a.depends:
        p["depends"] = pf.csv(a.depends)
    pf.write_phases(a, phases)
    return p
