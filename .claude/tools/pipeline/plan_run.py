"""plan.py run: a fresh headless architect session (Sonnet first, Opus only on its ESCALATE) that writes the plan only through plan.py; check and rerun."""

import os
import re
import shlex
import sys

from . import claude, metrics, plan_review
from .configuration import (
    ENV_NO_CONTEXT,
    ESCALATE_DRAFT_CHARS,
    EXIT_LIMIT,
    GAPS_FILE,
    MODEL_OPUS,
    MODEL_SONNET,
    PLAN_CONTEXT_FILE,
    PLAN_FILE,
    PLAN_REVIEW_GAPS_FILE,
    PLAN_REVIEW_REPORT_FILE,
    REQUEST_FILE,
    TASKS_FILE,
    project,
    repo_dir,
    tool_cmd,
)
from .ctx import Ctx
from .phase_context import brief_digest
from .plan_check import check_feature, check_stage, errors_of, publish_problems_in, publish_urls, repo_of, tasks_files
from .plan_files import feature_dir, read_phases
from .plan_testrun import testrun_stage
from .run import Run
from .util import TOOLS, read_text, swallowed, write_text

PLAN = tool_cmd("plan.py")
PLAN_FIX = "plan-fix"  # plan-step name and session tag of one plan-fix architect round; the summary counts them
EFFORT = {MODEL_SONNET: "medium", MODEL_OPUS: "high"}
# Checklist of the rules `plan.py check` enforces, in the architect brief: the architect fixes these before the check runs.
BRIEF_CHECKLIST = (
    "CHECKLIST (the rules `plan.py check` enforces; fix each before you reply):\n"
    "- Test line: one command that runs the task's own test file(s) from the project root; no bare absolute main-checkout paths.\n"
    "- Affected tests line: every existing test file the change could break, named by file.\n"
    "- Predicted paths: every file the task edits, relative to the project root.\n"
    "- Wave and depends: a task that uses code another task adds goes in a later wave, with `--depends` on that task.\n"
    "- Docs tasks: a docs task that cites new code runs in a later wave than that code, with depends on it; a docs task never shares a wave with the code it documents.\n"
    "- Size: each task fits one agent turn budget (about 25 turns); split a larger task.\n"
    f"- Run `{PLAN} check` on the plan and fix every problem it prints before you reply.\n"
)


def build_prompt(a, model, goal, extra="") -> str:
    F = a.feature
    small = (
        f"SMALL route (you expect <= 25 tasks and no gate below applies; one stage, cap 30): `{PLAN} stage-init --stage {F} --goal ...`, then one `{PLAN} task --stage {F} --id N --title T --paths p1,p2 --test CMD --done TEXT "
        "[--wave W] [--depends N,M] [--pattern file:line] [--turns 25]` per task; then "
        f"`{PLAN} check --stage {F}`."
    )
    big = (
        f"BIG route (more than ~25 tasks, or a gate applies): `{PLAN} plan-init --feature {F} --goal ...`, then one `{PLAN} phase --feature {F} --name N --goal G --done-when CMD --depends A,B --paths p1,p2 --tasks N` per phase "
        "(--tasks = your estimate of its tasks). Size a phase at about 15-25 tasks (cap 30): order inside it is the task graph, so cut a phase only at an uncertainty gate, an owner step or a real-data run; "
        "a phase under about 10 tasks starts its goal with `gate: <reason>`, and the reason is exactly one of: uncertainty gate, owner step or real-data run, separate repo, 30-task cap "
        '("a later phase follows" is not a gate). One phase alone is a stage: use the small route. '
        f"Then `{PLAN} check --feature {F}`."
    )
    return (
        f"Plan this request. Project dir: {os.path.abspath(a.dir)}; plan folder: .work/{F}. Model: {model}.\n"
        f"Decide the size yourself, then take exactly ONE route. {small} {big}\n"
        f"Write the plan ONLY through plan.py commands; reply VERDICT: DONE|BLOCKED|ESCALATE (ESCALATE with a reason only on Sonnet, when the request touches a contract, data format, schema, migration, save/progress format or scheduling logic: Opus then plans it). "
        f"Before replying ESCALATE write .work/{F}/ESCALATE.md for Opus: why you escalate, the files and facts you found (path and one line each), the decisions you already made, what is still open. Opus will not repeat your exploration. "
        f"Never hand-write TASKS.md, Plan.md or task-N.md.\n{BRIEF_CHECKLIST}{extra}\nREQUEST:\n{goal}"
    )


def verdict_of(text) -> str:
    if re.search(r"^ESCALATE\b", text or "", re.M):
        return "ESCALATE"
    m = re.findall(r"VERDICT:\s*(DONE|BLOCKED|ESCALATE)", text or "")
    return m[-1] if m else ""


def session(a, c, model, prompt, tag) -> str:
    step(c, tag, "start", model=model)
    u = claude.session(claude.claude_args("architect", model, EFFORT[model], prompt), a.dir)
    v = "ERROR" if u["error"] else verdict_of(u["result"])
    metrics.session_line(c, tag, "architect", model, u, verdict=v)
    write_text(os.path.join(feature_dir(a), "plan-last.md"), (u["result"] or "")[-2000:])
    step(c, tag, "done", model=model, verdict=v, secs=round(u.get("seconds", 0)), usd=round(u.get("cost_usd", 0), 4))
    return v


def step(c, name, state, **kw) -> None:
    """One plan-step line in metrics.jsonl (state start|done); `wave.py status --stage <feature>` prints them."""
    metrics.event(c, "plan-step", step=name, state=state, **kw)


def publish_problems(a, goal) -> list[str]:
    """-> problem when the request has a `PUBLISH:` line that no TASKS.md of the plan carries."""
    return publish_problems_in(feature_dir(a), goal)


def ensure_publish(a, goal) -> bool:
    """Append the request's missing `PUBLISH:` URLs to the plan's last TASKS.md (script, not agent: the architect may omit the line). -> True when it wrote."""
    d = feature_dir(a)
    if not publish_problems_in(d, goal):
        return False
    have = set().union(*(publish_urls(read_text(t)) for t in tasks_files(d)))
    todo = [u for u in sorted(publish_urls(goal)) if u not in have]
    files = [t for t in tasks_files(d) if os.path.exists(t)]
    if not todo or not files:
        return False
    text = read_text(files[-1])
    write_text(files[-1], text + ("" if text.endswith("\n") else "\n") + f"PUBLISH: {' '.join(todo)}\n")
    return True


def check_now(a, goal="") -> tuple[int, list[str]]:
    """Check what the architect wrote: Plan.md means the big route, else the one stage; plus the request's `PUBLISH:` line."""
    d = feature_dir(a)
    ensure_publish(a, goal)
    n, probs = check_feature(a, d) if os.path.exists(os.path.join(d, PLAN_FILE)) else check_stage(d)
    return n, [*probs, *publish_problems(a, goal)]


def testrun_problems(a) -> list[str]:
    """-> problems of `testrun` over every stage of the plan (Plan.md: each phase folder; else the one stage), run once per Test line."""
    d = feature_dir(a)
    dirs = [os.path.join(d, "phases", p["dir"]) for p in read_phases(a)] if os.path.exists(os.path.join(d, PLAN_FILE)) else [d]
    return [p for sd in dirs for p in testrun_stage(sd, repo_of(sd))[1]]


def explore_stats() -> dict:
    """Per-call numbers of the explore loop just run (agent_files.STATS) as `explore_*` metric fields; {} when it did not run."""
    st = getattr(sys.modules.get("agent_files"), "STATS", None)
    if not st or not st.get("ended"):
        return {}
    return {"explore_" + k: (round(v, 1) if isinstance(v, float) else v) for k, v in st.items()}


def code_context(a, goal) -> str:
    """Write .work/<feature>/plan-context.md (lp leads for the request) and -> the prompt sentence naming it, or "" when nothing could be made. Never raises."""
    if os.environ.get(ENV_NO_CONTEXT):
        return ""
    try:
        sys.path.insert(0, TOOLS)
        from localmodel import plan_context
        from search.ctx_config import ctx_config, ensure_index
        from search.leads_docs import code_files

        try:
            repo = repo_dir(a.dir, project(a.dir))
        except (OSError, ValueError):
            repo = a.dir
        out = os.path.join(feature_dir(a), "plan-context.md")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        cfg = ctx_config(a.dir)
        c = Ctx(Run(stage=a.feature, dir=a.dir))
        step(c, "context", "start")
        ensure_index(a.dir, cfg)
        write_text(out, plan_context.goal_context(repo, goal, cfg, out + ".explore.log"))
        text = read_text(out)
        tm = re.search(r"built in (\d+) s: index (\d+) s, local model (\d+) s", text)
        step(
            c,
            "context",
            "done",
            secs=int(tm[1]) if tm else 0,
            index_secs=int(tm[2]) if tm else 0,
            local_model_secs=int(tm[3]) if tm else 0,
            code_files=len(code_files(text)),
        )
        text = read_text(out)
        tm = re.search(r"built in (\d+) s: index (\d+) s, local model (\d+) s", text)
        metrics.event(
            Ctx(Run(stage=a.feature, dir=a.dir)),
            "context",
            chars=len(text),
            code_files=len(code_files(text)),
            secs_total=int(tm[1]) if tm else 0,
            secs_index=int(tm[2]) if tm else 0,
            secs_model=int(tm[3]) if tm else 0,
            local_model=os.environ.get("STRATA") != "0",
            **explore_stats(),
        )
        return f"Code context for this request, made by script with lp (read it first; `text hits` are an unverified word search): .work/{a.feature}/plan-context.md\n"
    except Exception as ex:
        swallowed("plan_run.code_context", ex)
        return ""


def blocked_report(a) -> str:
    """-> the text printed when the architect is BLOCKED: its OWNER.md, else plan-last.md."""
    return f"plan: BLOCKED\n{read_text(os.path.join(feature_dir(a), 'OWNER.md')) or read_text(os.path.join(feature_dir(a), 'plan-last.md'))}".rstrip()


def fix_goal(a, goal) -> str:
    """The request as a plan-fix architect gets it: a 1024-byte digest of request.md and the feature's plan-context.md (absent: request only)."""
    return brief_digest(goal, read_text(os.path.join(feature_dir(a), PLAN_CONTEXT_FILE)))


def testrun_fix(a, c, model, goal, tr) -> tuple[int, list[str]]:
    """Plan-fix session for the Test lines `testrun` rejects (`tr`), then -> (tasks, problems): check_now plus the testrun problems that remain."""
    session(
        a,
        c,
        model,
        build_prompt(
            a,
            model,
            fix_goal(a, goal),
            "The plan you wrote passed `check`, but `testrun` rejects these Test lines; fix them through plan.py commands:\n" + "\n".join(tr),
        ),
        PLAN_FIX,
    )
    n, probs = check_now(a, goal)
    return n, ([*probs, *testrun_problems(a)] if not errors_of(probs) else probs)


def testrun_check(a, c, model, goal, n, probs) -> tuple[int, list[str]]:
    """-> (tasks, problems) after `testrun` over the Test lines, but only when `check` found no errors; else unchanged."""
    if errors_of(probs) or not (tr := testrun_problems(a)):
        return n, probs
    return testrun_fix(a, c, model, goal, tr)


def review_fix(a, c, model, goal, report_path) -> None:
    """Plan-fix session for the GAP lines the plan review wrote to report_path."""
    extra = f"The plan review wrote {report_path}: fix every GAP line in it through plan.py commands.\n"
    session(a, c, model, build_prompt(a, model, fix_goal(a, goal), extra), PLAN_FIX)


def review_plan(a, c, model, goal, n, probs) -> tuple[int, list[str], list[str]]:
    """Plan review of a valid plan: phase review when Plan.md exists, else the one-phase review. One fix round on FAIL, then check again.
    -> (tasks, problems, gap lines); gaps never fail the plan. A reviewer error keeps the plan as it is."""
    d = feature_dir(a)
    kind = plan_review.KIND_PHASE if os.path.exists(os.path.join(d, PLAN_FILE)) else plan_review.KIND_SINGLE
    report_path = os.path.join(d, PLAN_REVIEW_REPORT_FILE)

    def dispatch(review_model, k) -> str:
        claude.session(claude.claude_args("plan-reviewer", review_model, None, plan_review.build_prompt(k, a.feature, report_path)), a.dir, None)
        return read_text(report_path)

    def fix(_report) -> None:
        review_fix(a, c, model, goal, report_path)

    step(c, "plan-review", "start", kind=kind)
    res = plan_review.run_review(kind, dispatch, fix, n_tasks=n)
    step(c, "plan-review", "done", kind=kind, verdict=res.verdict, gaps=len(res.gaps), rounds=res.rounds, model=res.model)
    if res.rounds:
        n, probs = check_now(a, goal)
    return n, probs, [f"plan-review: gap {g.removeprefix('GAP:').strip()}" for g in res.gaps]


def escalation_note(a) -> str:
    """Opus brief lines after a Sonnet ESCALATE: its reason (the end of its reply, plan-last.md) and its draft (Plan.md, TASKS.md, cut), with the rule to amend, not replan."""
    fd = feature_dir(a)
    reason = read_text(os.path.join(fd, "plan-last.md")).strip()
    draft = "".join(f"--- {name}\n{read_text(os.path.join(fd, name))[:ESCALATE_DRAFT_CHARS]}\n" for name in (PLAN_FILE, TASKS_FILE))
    return (
        f"Sonnet escalated this request to you. Its reason (the end of its reply): {reason}\n"
        f"Its draft, cut at {ESCALATE_DRAFT_CHARS} chars per file:\n{draft}"
        "Amend that draft to fix the reason above; do not replan from scratch.\n"
    )


def _architect_session(a, c, model, goal, extra):
    """Run the architect; a Sonnet ESCALATE reruns it once on Opus with the handoff. -> (model used, verdict)."""
    v = session(a, c, model, build_prompt(a, model, goal, extra), "plan-opus" if model == MODEL_OPUS else "plan")
    if v == "ESCALATE" and model == MODEL_SONNET:
        model = MODEL_OPUS
        hand = os.path.join(feature_dir(a), "ESCALATE.md")
        if os.path.exists(hand):
            extra += f"Sonnet escalated this request to you and left a handoff: read .work/{a.feature}/ESCALATE.md first, trust its facts, and do not repeat its exploration.\n"
        extra += escalation_note(a)
        v = session(a, c, model, build_prompt(a, model, goal, extra), "plan-opus")
    return model, v


def _plan_fix(a, c, model, goal, probs):
    """One plan-fix session: the architect repairs the plan through plan.py after `check` failed."""
    session(
        a,
        c,
        model,
        build_prompt(a, model, fix_goal(a, goal), "The plan you wrote failed `check`; fix it through plan.py commands:\n" + "\n".join(probs)),
        PLAN_FIX,
    )


def run(a) -> int:
    """-> exit code: 0 plan valid, 2 blocked or still invalid after one rerun, EXIT_LIMIT (3) plan valid but plan-review gaps remain after the fix rounds."""
    goal = read_text(a.prompt_file)
    model = getattr(a, "model", MODEL_SONNET) or MODEL_SONNET  # --model opus: known ahead to need Opus, skip the Sonnet pass
    if a.dry_run:
        prompt = build_prompt(a, model, goal, "")
        cmd = ["claude", *claude.claude_args("architect", model, EFFORT[model], prompt)]
        print(f"model: {model}\n" + prompt + "\n\n" + " ".join(shlex.quote(x) for x in cmd[:-1]) + " <prompt>")
        return 0
    write_text(os.path.join(feature_dir(a), REQUEST_FILE), goal)
    c = Ctx(Run(stage=a.feature, dir=a.dir))
    step(c, "startup", "start")
    extra = code_context(a, goal)
    step(c, "startup", "done")
    model, v = _architect_session(a, c, model, goal, extra)
    if v == "BLOCKED":
        print(blocked_report(a))
        return 2
    step(c, "check", "start")
    n, probs = check_now(a, goal)
    step(c, "check", "done", tasks=n, errors=len(errors_of(probs)))
    if errors_of(probs):
        _plan_fix(a, c, model, goal, probs)
        n, probs = check_now(a, goal)
    n, probs = testrun_check(a, c, model, goal, n, probs)
    if errors_of(probs):
        print("\n".join(probs))
        return 2
    n, probs, gaps = review_plan(a, c, model, goal, n, probs)
    if errors_of(probs):
        print("\n".join(probs))
        return 2
    rounds = sum(1 for s in c.sess if s["id"] == PLAN_FIX)
    print("\n".join([*probs, f"OK {n} tasks (plan-fix rounds: {rounds})"]))
    return _gaps_block(a, gaps) if gaps else _clear_gaps(a)


def _gaps_block(a, gaps) -> int:
    """Plan-review gaps still open after the fix rounds: the GAPS: block as the run's last output, also written to GAPS_FILE. The stale PLAN_REVIEW_GAPS_FILE goes: the final pass owns the gaps. -> EXIT_LIMIT."""
    block = "\n".join(["GAPS:", *gaps])
    write_text(os.path.join(feature_dir(a), GAPS_FILE), block + "\n")
    _drop_review_gaps(a)
    print(block)
    return EXIT_LIMIT


def _clear_gaps(a) -> int:
    """No gaps left: drop the GAPS_FILE and the PLAN_REVIEW_GAPS_FILE of an earlier run so they do not outlive their gaps. -> 0."""
    _drop(os.path.join(feature_dir(a), GAPS_FILE))
    _drop_review_gaps(a)
    return 0


def _drop_review_gaps(a) -> None:
    """Remove PLAN_REVIEW_GAPS_FILE from the feature root and from every phase folder: cli_feature writes it into phases/<dir>."""
    d = feature_dir(a)
    for folder in [d, *(os.path.join(d, "phases", p["dir"]) for p in read_phases(a))]:
        _drop(os.path.join(folder, PLAN_REVIEW_GAPS_FILE))


def _drop(path) -> None:
    """Remove path when it exists."""
    if os.path.exists(path):
        os.remove(path)
