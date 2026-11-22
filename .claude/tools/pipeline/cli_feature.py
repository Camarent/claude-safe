"""`cs-wave feature --feature F`: run a big plan (Plan.md with phases) phase after phase.

For each open phase, in dependency order: plan its tasks when TASKS.md is missing (one architect session, so a later phase sees what earlier ones built),
run the stage like `cs-wave run` does, run the phase's `done_when` command, mark the phase done. Stops and returns to the lead (exit 4) on any block:
planning that does not check out, a stage that asks the lead (the last phase ends with the master-merge pause), a failed `done_when`. Exit 3: a limit of the stage run
was hit. Re-running continues where it stopped: the state lives in each phase's .work folder and in Plan.md. --dry-run prints what would happen and changes nothing."""

import os
import subprocess
import sys
import time

from . import claude, cli_wave, gitx, metrics, plan_check, plan_review, pr_ops, progress
from . import plan_files as pf
from .branches import dirty_block, ensure_work_branch, red_base_block
from .briefs import NAV
from .cli_plan import mark_phase_done
from .configuration import (
    AUDITS_DIR,
    BLOCKED_FILE,
    CARRIED_NOT_NEEDED,
    CARRIED_SECTION,
    DEVIATION_TAG,
    DOCS_STALE_TAG,
    ENV_NO_CONTEXT,
    EXIT_BLOCKED,
    FAIL,
    FEATURE_LOCK,
    LONG_CMD_TIMEOUT_S,
    PHASES_DIR,
    PLAN_FILE,
    PLAN_REVIEW_GAPS_FILE,
    PLAN_REVIEW_REPORT_FILE,
    REPORTS_DIR,
    project,
    repo_dir,
    test_env,
    work_branch,
)
from .ctx import Ctx
from .plan_rules import manual_tag
from .run import Run
from .util import TOOLS, default_dir, read_text, swallowed, write_text


def ctx_path(a, phase):
    return os.path.join(pf.feature_dir(a), "phases", phase["dir"], "plan-context.md")


def ctx_sentence(a, phase):
    if not os.path.exists(ctx_path(a, phase)):
        return ""
    return f"Read .work/{a.feature}/phases/{phase['dir']}/plan-context.md first: a script-made digest (decisions, what earlier phases merged, likely files); treat it as hints to verify, not as truth. "


def carried_sentence(a, phase):
    """For a phase after the first: the rule to read the earlier phases' reports, audits and TASKS.md, and to write the carried-items section. "" for the first phase."""
    from .phase_context import earlier_rows

    earlier = earlier_rows(pf.feature_dir(a), phase)
    if not earlier:
        return ""
    where = ", ".join(f"{PHASES_DIR}/{r['dir']}/{REPORTS_DIR}" for r in earlier)
    audits = ", ".join(f"{PHASES_DIR}/{r['dir']}/{AUDITS_DIR}" for r in earlier)
    return (
        f"Before you plan, read what the earlier phases left: the {FAIL}, {DEVIATION_TAG} and {DOCS_STALE_TAG} lines of their reports ({where}), their audits ({audits}), "
        f"and their TASKS.md (the context digest, when given above, summarizes them; verify it). Then write a `## {CARRIED_SECTION}` section in this phase's TASKS.md: one bullet per item carried from them, "
        f"either a task of this phase (`- task-N ...`) or `- {CARRIED_NOT_NEEDED} reason`. plan.py check rejects a later phase TASKS.md without that section. "
    )


def repo_phase(a, phase):
    """PHASE with its `paths` made repo-root-relative: Plan.md names them from the workspace (`myrepo/src`), but the planner's context and the devs see the repo root (worktree root), so the repo prefix must not leak into task files."""
    repo = (project(a.dir).get("repo") or "").strip("/")
    pre = repo + "/"
    return {**phase, "paths": [p[len(pre) :] if repo and p.startswith(pre) else ("." if p == repo else p) for p in phase["paths"]]}


def phase_ctx(a, phase):
    """Write the phase's plan-context.md and log a `context` event. Best effort: never raises, never blocks the planner."""
    if os.environ.get(ENV_NO_CONTEXT):
        return
    try:
        phase = repo_phase(a, phase)
        sys.path.insert(0, TOOLS)
        from localmodel import plan_context
        from search.ctx_config import ctx_config
        from search.leads_docs import code_files

        from .plan_run import explore_stats

        st = getattr(sys.modules.get("agent_files"), "STATS", None)
        if st is not None:
            st.clear()  # a stale run of an earlier phase must not be logged for this one
        t0 = time.time()
        cfg = ctx_config(a.dir)
        proj = project(a.dir)
        repo = repo_dir(a.dir, proj)
        text = plan_context.phase_context(repo, pf.feature_dir(a), phase, cfg)
        out = ctx_path(a, phase)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        write_text(out, text)
        metrics.event(
            Ctx(Run(stage=a.feature, dir=a.dir)),
            "context",
            phase=phase["dir"],
            chars=len(text),
            code_files=len(code_files(text)),
            secs_total=int(time.time() - t0),
            **explore_stats(),
        )
    except Exception as ex:
        swallowed("cli_feature.phase_ctx", ex)
        return


def plan_prompt(a, phase):
    S = pf.stage_of(a.feature, phase)
    phase = repo_phase(a, phase)
    return (
        f"Plan ONE phase of the big plan in .work/{a.feature}/Plan.md: phase `{phase['dir']}`, goal: {phase['goal']}. It owns the paths {', '.join(phase['paths']) or '(see Plan.md)'}; "
        f"done when: `{phase['done_when']}`. {ctx_sentence(a, phase)}Earlier phases are already built and merged into the work branch: read what they left (git log, their TASKS.md under .work/{a.feature}/phases/) "
        f"so this phase builds on it. {carried_sentence(a, phase)}Write the stage with plan.py as `--stage {S}`: `stage-init` (pass only the flags this phase needs, such as ui or docs; do not copy the flags line of an earlier phase and never add no-audit, no-push or other no-* flags: the pipeline decides audits and the publish tail from the phase order), then one `task` per unit of work, "
        f"parallel only when paths do not overlap, then `plan.py check --stage {S}` until it prints OK. Reply `VERDICT: DONE` or `VERDICT: BLOCKED` with the reason."
        + NAV
    )


def plan_phase(a, phase):
    """Architect session for one phase. -> problems list ([] = planned and valid)."""
    folder = os.path.join(pf.feature_dir(a), "phases", phase["dir"])
    print(progress.phase_line(folder, "planning started: building the phase context"), flush=True)
    t = time.time()
    phase_ctx(a, phase)
    print(progress.phase_line(folder, f"phase context done in {time.time() - t:.0f}s, architect planning"), flush=True)
    res = claude.session(claude.claude_args("architect", None, None, plan_prompt(a, phase)), a.dir, None)
    if not os.path.exists(pf.tasks_path(folder)):
        why = ""
        if res.get("denials"):
            why += "; permission denied: " + " | ".join(res["denials"])
        if res.get("result"):
            why += "; session said: " + res["result"][:200].replace("\n", " ")
        return [f"{phase['dir']}: the architect wrote no TASKS.md{why}"]
    n, found = plan_check.check_stage(folder)
    probs = plan_check.errors_of(found)
    for w in found:
        if w not in probs:
            print(w, flush=True)  # warnings (shared:, size:, publish) never block, same as `cs-plan check`
    metrics.event(Ctx(Run(stage=a.feature, dir=a.dir)), "phase-size", phase=phase["dir"], estimate=phase.get("tasks", 0), actual=n)
    print(progress.phase_line(folder, f"planned: {n} tasks" + (f", {len(probs)} problems" if probs else "")), flush=True)
    if probs:
        return probs
    return _review_plan(a, phase, folder, n)


def _review_kind(a, phase):
    """`cover` for the last phase with tasks (the open phase right before the finish phase), else `task`."""
    dirs = [p["dir"] for p in pf.read_phases(a)]
    i = dirs.index(phase["dir"]) if phase["dir"] in dirs else len(dirs)
    return plan_review.KIND_COVER if i + 1 < len(dirs) and pf.is_finish_dir(dirs[i + 1]) else plan_review.KIND_TASK


def _review_plan(a, phase, folder, n):
    """Plan review after the checks pass: one reviewer report, one architect fix round on FAIL, then a re-check. -> problems ([] = go on).
    The cover review may refuse the plan (BLOCKED: another phase is needed); leftover gaps go to PLAN_REVIEW_GAPS_FILE and the phase continues."""
    kind = _review_kind(a, phase)
    report_path = os.path.join(folder, PLAN_REVIEW_REPORT_FILE)

    def dispatch(model, k):
        claude.session(claude.claude_args("plan-reviewer", model, None, plan_review.build_prompt(k, a.feature, report_path)), a.dir, None)
        return read_text(report_path)

    def fix(report):
        note = f"\n\nThe plan review found these gaps; fix them in the task files and run `plan.py check --stage {pf.stage_of(a.feature, phase)}` until it prints OK:\n{report}"
        claude.session(claude.claude_args("architect", None, None, plan_prompt(a, phase) + note), a.dir, None)

    limit = int(project(a.dir).get("max_tasks") or 0)
    res = plan_review.run_review(kind, dispatch, fix, n_tasks=n, limit=limit)
    if kind == plan_review.KIND_COVER and res.verdict == plan_review.VERDICT_BLOCKED:
        return [f"plan-review BLOCKED: another phase needed: {'; '.join(res.gaps[:10]) or 'the cover review found the request does not fit this plan'}"]
    if res.rounds:
        _, found = plan_check.check_stage(folder)
        probs = plan_check.errors_of(found)
        if probs:
            return probs
    if res.gaps:
        write_text(os.path.join(folder, PLAN_REVIEW_GAPS_FILE), "\n".join(res.gaps) + "\n")
        print(f"plan-review {kind}: {len(res.gaps)} gap(s) left, written to {PLAN_REVIEW_GAPS_FILE}:\n" + "\n".join(res.gaps[:10]), flush=True)
    return []


def run_done_when(a, phase):
    """-> (ok, tail of output). Runs the phase's done_when in the repo folder with the project's test_env."""
    cfg = project(a.dir)
    repo = repo_dir(a.dir, cfg)
    env = {**os.environ, **test_env(cfg, a.dir)}
    try:
        r = subprocess.run(phase["done_when"], shell=True, cwd=repo, env=env, capture_output=True, text=True, timeout=LONG_CMD_TIMEOUT_S, check=False)  # nosec B602  # runs a command from the plan, authored by the architect; the pipeline trusts the plan
        return r.returncode == 0, (r.stdout + r.stderr)[-600:]
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def block(a, message):
    write_text(os.path.join(pf.feature_dir(a), BLOCKED_FILE), message + "\n")
    print(f"BLOCKED, back to lead (also in {pf.feature_dir(a)}/BLOCKED.md):\n{message}")
    return EXIT_BLOCKED


def _dry_feature(a, ns):
    """--dry-run: walk every open phase in order; nothing is planned, run or marked."""

    for p in (x for x in pf.read_phases(ns) if x["status"] != "done"):
        folder = os.path.join(pf.feature_dir(a), "phases", p["dir"])
        planned = os.path.exists(pf.tasks_path(folder))
        print(
            f"== phase {p['dir']}: {p['goal']} (after {', '.join(p['depends']) or 'nothing'})"
            + ("" if planned else "  NOT PLANNED: an architect session would plan it first")
        )
        if planned:
            cli_wave.dry_walk(Run.of(a).at(stage=pf.stage_of(a.feature, p)))
        print(f"   then done_when: {p['done_when']}")
    phases = pf.read_phases(ns)
    if phases and not any(pf.is_finish_dir(x["dir"]) for x in phases):
        print(f"== phase {int(phases[-1]['dir'][:2]) + 1:02d}-finish: the driver adds it: tests, audits, master merge, push, cleanup, feature retro")
    return 0


def _pick_phase(a, ns):
    """-> (next open phase, None), or (None, exit code) when the feature is finished or stuck."""
    phases = pf.read_phases(ns)
    if not phases:
        d = pf.feature_dir(a)
        if not os.path.exists(os.path.join(d, PLAN_FILE)) and not os.path.exists(pf.tasks_path(d)):
            return None, block(a, f"no plan: run plan.py run first (neither Plan.md nor TASKS.md in .work/{a.feature})")
        return None, block(a, f"no phases in .work/{a.feature}/Plan.md")
    phase = pf.next_open_phase(phases)
    if phase is not None:
        return phase, None
    left = [p["dir"] for p in phases if p["status"] != "done"]
    if left:
        return None, block(a, f"phases {', '.join(left)} wait on a dependency that is not done")
    print(f"feature {a.feature}: all {len(phases)} phases done")
    return None, 0


def _record_base(a, folder, phase):
    """Write `base.txt` (the work branch head at the phase start) once; audit context and recall measure the phase from it. Not for the finish phase: it audits the whole feature against the release branch."""
    f = os.path.join(folder, "base.txt")
    if pf.is_finish_dir(phase["dir"]) or os.path.exists(f):
        return
    cfg = project(a.dir)
    r = gitx.run(repo_dir(a.dir, cfg), "rev-parse", "--verify", "-q", work_branch(cfg, getattr(a, "feature", None) or a.stage))
    if r.returncode == 0 and r.stdout.strip():
        write_text(f, r.stdout.strip() + "\n")


def _run_phase(a, phase):
    """Plan (when needed), run and close one phase. -> None when it is done, else the exit code."""

    folder = os.path.join(pf.feature_dir(a), "phases", phase["dir"])
    S = pf.stage_of(a.feature, phase)
    if pf.is_finish_dir(phase["dir"]) and not os.path.exists(pf.tasks_path(folder)):
        os.makedirs(folder, exist_ok=True)
        write_text(
            pf.tasks_path(folder),
            f"# {S}\n\nThe closing phase: no tasks of its own. It runs the tests, audits, master merge, push, index, cleanup and the feature retro for all phases before it.\n",
        )
    if not os.path.exists(pf.tasks_path(folder)):
        print(f"== planning {S}")
        problems = plan_phase(a, phase)
        if problems:
            return block(a, f"phase {phase['dir']} could not be planned cleanly:\n" + "\n".join(problems[:10]))
    _record_base(a, folder, phase)
    print(f"== running {S}")
    rc = cli_wave.cmd_run(Run.of(a).at(stage=S))
    if rc:
        return rc  # 3 limit, 4 asks the lead (also the last phase's master-merge pause)
    manual = manual_tag(phase["done_when"])
    if manual is not None:
        return block(a, f"phase {phase['dir']}: manual check for the lead (not run by the driver): {manual}\nWhen it holds, mark the phase done and rerun.")
    ok, out = run_done_when(a, phase)
    if not ok:
        return block(a, f"phase {phase['dir']}: done_when failed: `{phase['done_when']}`\n{out}")
    mark_phase_done(Run(dir=a.dir, feature=a.feature), phase["dir"])
    print(f"== phase {phase['dir']} done")
    return None


def _single_stage(a, ns):
    """A small plan: TASKS.md in the feature folder and no phases in Plan.md."""
    return not pf.read_phases(ns) and os.path.exists(pf.tasks_path(pf.feature_dir(a)))


def cmd_feature(a):
    if a.dry_run:
        return _feature(a)
    os.makedirs(pf.feature_dir(a), exist_ok=True)
    lock = os.path.join(pf.feature_dir(a), FEATURE_LOCK)
    if not cli_wave._take_lock(lock):
        print(f"blocked: wave.py feature already runs {a.feature} ({lock})")
        return 4
    try:
        return _feature(a)
    finally:
        cli_wave._release_lock(lock)


def _feature(a):
    from search.strata import preflight

    preflight()
    if msg := dirty_block(a):
        print(msg)
        return 4
    if msg := red_base_block(a):
        print(msg)
        return 4
    pr_ops.sweep_before_branch(a)
    if msg := ensure_work_branch(a, a.feature):
        print(msg)
        return 4
    ns = Run(dir=a.dir, feature=a.feature)
    if not a.dry_run and pf.ensure_finish(ns):
        print(f"== added the closing phase to .work/{a.feature}/Plan.md")
    if _single_stage(a, ns):
        stage = Run.of(a).at(stage=a.feature)
        print(f"== single-stage plan: running {a.feature}")
        return cli_wave.dry_walk(stage) if a.dry_run else cli_wave.cmd_run(stage)
    if a.dry_run:
        return _dry_feature(a, ns)
    while True:
        phase, rc = _pick_phase(a, ns)
        if phase is None:
            return rc
        rc = _run_phase(a, phase)
        if rc:
            return rc


def add_feature_command(sub):
    p = sub.add_parser("feature")
    p.add_argument("--feature", required=True)
    p.add_argument("--dir", default=default_dir())
    p.add_argument("--max-waves", type=int, default=None)
    p.add_argument("--max-dispatches", type=int, default=None)
    p.add_argument("--max-sessions", type=int, default=None)
    p.add_argument("--max-budget-usd", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-dirty", action="store_true")
    p.set_defaults(fn=cmd_feature)
