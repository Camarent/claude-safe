"""plan.py run: a fresh headless architect session (Sonnet first, Opus only on its ESCALATE) that writes the plan only through plan.py; check and rerun."""

import os
import re
import shlex
import sys

from . import claude, metrics
from .configuration import ENV_NO_CONTEXT, MODEL_OPUS, MODEL_SONNET, PLAN_FILE, REQUEST_FILE, project, repo_dir, tool_cmd
from .ctx import Ctx
from .plan_check import check_feature, check_stage, errors_of, publish_problems_in, publish_urls, repo_of, tasks_files
from .plan_files import feature_dir, read_phases
from .plan_testrun import testrun_stage
from .run import Run
from .util import TOOLS, read_text, swallowed, write_text

PLAN = tool_cmd("plan.py")
EFFORT = {MODEL_SONNET: "medium", MODEL_OPUS: "high"}


def build_prompt(a, model, goal, extra=""):
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
        f"Never hand-write TASKS.md, Plan.md or task-N.md.\n{extra}\nREQUEST:\n{goal}"
    )


def verdict_of(text):
    if re.search(r"^ESCALATE\b", text or "", re.M):
        return "ESCALATE"
    m = re.findall(r"VERDICT:\s*(DONE|BLOCKED|ESCALATE)", text or "")
    return m[-1] if m else ""


def session(a, c, model, prompt, tag):
    step(c, tag, "start", model=model)
    u = claude.session(claude.claude_args("architect", model, EFFORT[model], prompt), a.dir)
    v = "ERROR" if u["error"] else verdict_of(u["result"])
    metrics.session_line(c, tag, "architect", model, u, verdict=v)
    write_text(os.path.join(feature_dir(a), "plan-last.md"), (u["result"] or "")[-2000:])
    step(c, tag, "done", model=model, verdict=v, secs=round(u.get("seconds", 0)), usd=round(u.get("cost_usd", 0), 4))
    return v


def step(c, name, state, **kw):
    """One plan-step line in metrics.jsonl (state start|done); `wave.py status --stage <feature>` prints them."""
    metrics.event(c, "plan-step", step=name, state=state, **kw)


def publish_problems(a, goal):
    """-> problem when the request has a `PUBLISH:` line that no TASKS.md of the plan carries."""
    return publish_problems_in(feature_dir(a), goal)


def ensure_publish(a, goal):
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


def check_now(a, goal=""):
    """Check what the architect wrote: Plan.md means the big route, else the one stage; plus the request's `PUBLISH:` line."""
    d = feature_dir(a)
    ensure_publish(a, goal)
    n, probs = check_feature(a, d) if os.path.exists(os.path.join(d, PLAN_FILE)) else check_stage(d)
    return n, [*probs, *publish_problems(a, goal)]


def testrun_problems(a):
    """-> problems of `testrun` over every stage of the plan (Plan.md: each phase folder; else the one stage), run once per Test line."""
    d = feature_dir(a)
    dirs = [os.path.join(d, "phases", p["dir"]) for p in read_phases(a)] if os.path.exists(os.path.join(d, PLAN_FILE)) else [d]
    return [p for sd in dirs for p in testrun_stage(sd, repo_of(sd))[1]]


def explore_stats():
    """Per-call numbers of the explore loop just run (agent_files.STATS) as `explore_*` metric fields; {} when it did not run."""
    st = getattr(sys.modules.get("agent_files"), "STATS", None)
    if not st or not st.get("ended"):
        return {}
    return {"explore_" + k: (round(v, 1) if isinstance(v, float) else v) for k, v in st.items()}


def code_context(a, goal):
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


def run(a):
    """-> exit code: 0 plan valid, 2 blocked or still invalid after one rerun."""
    goal = read_text(a.prompt_file)
    if not a.dry_run:
        write_text(os.path.join(feature_dir(a), REQUEST_FILE), goal)
    extra = "" if a.dry_run else code_context(a, goal)
    model = getattr(a, "model", MODEL_SONNET) or MODEL_SONNET  # --model opus: known ahead to need Opus, skip the Sonnet pass
    prompt = build_prompt(a, model, goal, extra)
    if a.dry_run:
        cmd = ["claude", *claude.claude_args("architect", model, EFFORT[model], prompt)]
        print(f"model: {model}\n" + prompt + "\n\n" + " ".join(shlex.quote(x) for x in cmd[:-1]) + " <prompt>")
        return 0
    c = Ctx(Run(stage=a.feature, dir=a.dir))
    v = session(a, c, model, prompt, "plan-opus" if model == MODEL_OPUS else "plan")
    if v == "ESCALATE" and model == MODEL_SONNET:
        model = MODEL_OPUS
        hand = os.path.join(feature_dir(a), "ESCALATE.md")
        if os.path.exists(hand):
            extra += f"Sonnet escalated this request to you and left a handoff: read .work/{a.feature}/ESCALATE.md first, trust its facts, and do not repeat its exploration.\n"
        prompt = build_prompt(a, model, goal, extra)
        v = session(a, c, model, prompt, "plan-opus")
    if v == "BLOCKED":
        print(f"plan: BLOCKED\n{read_text(os.path.join(feature_dir(a), 'OWNER.md')) or read_text(os.path.join(feature_dir(a), 'plan-last.md'))}".rstrip())
        return 2
    n, probs = check_now(a, goal)
    step(c, "check", "done", tasks=n, errors=len(errors_of(probs)))
    if errors_of(probs):
        session(
            a, c, model, build_prompt(a, model, goal, "The plan you wrote failed `check`; fix it through plan.py commands:\n" + "\n".join(probs)), "plan-fix"
        )
        n, probs = check_now(a, goal)
    if not errors_of(probs) and (tr := testrun_problems(a)):
        session(
            a,
            c,
            model,
            build_prompt(
                a,
                model,
                goal,
                "The plan you wrote passed `check`, but `testrun` rejects these Test lines; fix them through plan.py commands:\n" + "\n".join(tr),
            ),
            "plan-fix",
        )
        n, probs = check_now(a, goal)
        probs = [*probs, *testrun_problems(a)] if not errors_of(probs) else probs
    if errors_of(probs):
        print("\n".join(probs))
        return 2
    print("\n".join([*probs, f"OK {n} tasks"]))
    return 0
