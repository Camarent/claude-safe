"""Same-wave candidates for plan_check: two tasks in different waves whose Predicted paths are disjoint and that no depends chain orders could share a wave. A `parallel:` warning only (never fails a check)."""

from .plan_files import task_path_modes, wave_conflict
from .plan_rules import _transitive_deps

PARALLEL_WARN_PREFIX = "parallel: "  # a warning line: never fails a check (errors_of)


def parallel_candidates(d, ents) -> list[str]:
    """-> one `parallel:` warning per pair of tasks in different waves, with Predicted paths that do not overlap (plan_files.wave_conflict) and neither of which depends on the other, directly or through others. Same-wave pairs are not listed: they already share a wave. A task with no Predicted paths is not listed: it claims no path to be disjoint from."""
    modes = {e["id"]: task_path_modes(d, e["id"]) for e in ents}
    held = {e["id"]: _transitive_deps(ents, e["id"]) for e in ents}
    out = []
    for i, a in enumerate(ents):
        for b in ents[i + 1 :]:
            if a["wave"] == b["wave"]:
                continue
            early, late = (a, b) if a["wave"] < b["wave"] else (b, a)
            if not modes[early["id"]] or not modes[late["id"]]:
                continue
            if early["id"] in held[late["id"]] or late["id"] in held[early["id"]]:
                continue
            if wave_conflict(modes[early["id"]], modes[late["id"]]):
                continue
            out.append(
                f"{PARALLEL_WARN_PREFIX}{early['id']} (wave {early['wave']}) and {late['id']} (wave {late['wave']}) touch disjoint paths and no depends chain orders them: could share a wave"
            )
    return out
