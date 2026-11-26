"""Where a stage's `.work/<stage>` dir lives. Stdlib only. The callers keep thin wrappers (ops_steps.stage_dir, plan_files.stage_dir, util.stage_root, plan_check._workspace)."""

import glob
import os
import re

WORK = ".work"
STAGE_NAME_RE = re.compile(r"\w[\w.-]*(/phases/\d+[\w.-]*)?")  # `<feature>` or `<feature>/phases/<n>`


def _bad_name(stage):
    parts = stage.split("/")
    return not stage.strip("/") or os.pardir in parts or os.curdir in parts or os.path.isabs(stage)


def resolve_stage(d, stage, strict=True, phases=False) -> str:
    """-> `<d>/.work/<stage>`.
    strict: D becomes absolute and a STAGE that is empty, absolute or holds a `.`/`..` part raises ValueError. Lenient (strict=False): any name, D as given.
    phases: when that dir does not exist, the first `.work/*/phases/<stage>` that does (S may be a bare phase name)."""
    if strict:
        if _bad_name(stage):
            raise ValueError(f"bad --stage {stage!r}: a folder under {WORK}")
        d = os.path.abspath(d)
    base = os.path.join(d, WORK)
    direct = os.path.join(base, stage)
    if phases and not os.path.isdir(direct):
        hit = sorted(glob.glob(os.path.join(base, "*", "phases", stage)))
        return hit[0] if hit else direct
    return direct


def stage_root(d, stage) -> str | None:
    """-> the nearest dir upward from `d` (first; then the cwd) that holds `.work/<stage>`, or None. A bad STAGE name (`..`, absolute, other separators) gives None.
    The walk stops after the first dir holding `pipeline.json` and after the second git repo root (a nested repo sits inside its workspace), never reaching `/`."""
    if not STAGE_NAME_RE.fullmatch(stage or "") or ".." in stage:
        return None
    for start in dict.fromkeys((os.path.abspath(d), os.getcwd())):
        cur, repos = start, 0
        while True:
            if os.path.isdir(os.path.join(cur, WORK, stage)):
                return cur
            repos += os.path.exists(os.path.join(cur, ".git"))
            up = os.path.dirname(cur)
            if up == cur or repos >= 2 or os.path.isfile(os.path.join(cur, "pipeline.json")):
                break
            cur = up
    return None


def workspace_of(d) -> str:
    """The dir holding .work (where pipeline.json lives): D or the nearest parent named `.work`, one level up."""
    base = os.path.abspath(d)
    while os.path.basename(base) != WORK and os.path.dirname(base) != base:
        base = os.path.dirname(base)
    return os.path.dirname(base)
