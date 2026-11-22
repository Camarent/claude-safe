"""Thin orchestrator: the main Claude session executes, this script decides. No model calls in here.

    python3 -I /root/.claude/tools/orch.py next   --stage s1 [--dir .]
    python3 -I /root/.claude/tools/orch.py report --stage s1 --id a3 [--dir .]      (no text: the agent wrote .work/s1/reports/report-a3.md)
    python3 -I /root/.claude/tools/orch.py leads --stage s1 [--key plan] --text "goal"     (local.py find hits for a brief, cached in .work/s1/leads/leads-<key>.md)
    python3 -I /root/.claude/tools/orch.py triggers [--stage s1]     (log of every Opus-class / high-effort call: why, outcome, important?)
    python3 -I /root/.claude/tools/orch.py retro --stage s1          (retro signals from the logs; `done` already says "retro needed" when any tripped)
    python3 -I /root/.claude/tools/orch.py retry|skip --stage s1 --step task-2|merge-w1|final-test|audit-sec|fix-1 [--from review] [--note "what the lead changed"] [--addendum "text for the task file"]
        (--addendum, retry only: appends an `## ADDENDUM <UTC time>` block to the step's task file and sets the note; script steps have no task file and refuse it)
        (--from review: dev is DONE, only the reviewer reruns on the existing dev result)

`next` prints ONE json line: {"action":"dispatch","dispatches":[{"id":"a3","agent":"dev","model":"haiku","prompt":"..."}, ...]}
or {"action":"done"|"ask_main","message":"..."}. All dispatches of one `next` are independent (one Graph wave): the main session
starts them together in ONE message, then calls `report` for each id. It never reads, summarizes or retypes agent output: the
agents write full reports to files and reply with one fixed line, so its context stays flat. Agents may use local.py for search.
The Graph, the rounds and the prompts are plain code (from .work/<stage>/TASKS.md). A verdict is the first line `VERDICT: X` of the
agent's report (dev: DONE|ESCALATE|BLOCKED, reviewer: PASS|FAIL|ESCALATE); without it, an `ESCALATE:` line or plain heuristics apply and
an unclear report takes the safe verdict (BLOCKED/FAIL), so a bad guess costs at most a round. 2 failed rounds go to the main session.
Extra agents (any type) can be requested by file by whoever writes it (a planner, the main session): a brief file and
.work/<stage>/requests/<name>.json {"agent":"researcher","model":"haiku"|null,"brief":".work/<stage>/brief-x.md"};
`next` validates it (agent exists in the agents dir, brief inside .work/<stage>/, dispatch budget, gate for outward-facing
agents) and emits the normal dispatch. Rejections are logged as events.
Phases (flags line in TASKS.md: `flags: ui docs index push merge test audit retro clean publish audit-low audit-high`): per wave dev -> reviewer, then `merger` squash-merges the wave into
dev and runs the tests on top; after ALL waves: tester (+web-tester with ui), docs-writer (with docs), then audit-run (script, if AUDIT.md has audit-tools), sec || ux (ux only with ui + a ```ux block in AUDIT.md), then arch always (reads their reports).
Audit verdict CLEAN | FIXES | BLOCKED: FIXES starts ONE fix cycle (architect appends fix tasks + a new wave to TASKS.md, they run as normal waves: dev -> reviewer -> merger, then only the audits that said FIXES run again, Haiku low if all fix tasks were mechanical), a second FIXES
or any BLOCKED/FAIL anywhere goes to the main session (`ask_main`). The main session (the lead) may fix the cause itself (read the named report, edit,
run a command), then `retry --step X --note TEXT` (X runs again with the note in front of its prompt) or `skip --step X`; `/run-feature` goes on from that same place.
After the audits: `master-merged` (script `pr_steps.py merge` squash-merges a green PR; otherwise a pause: the lead squash-merges the stage branch into master, then `orch.py skip --step master-merged`) -> `push-master` (merger, skipped by `no-push`/`no-merge`) -> `republish` (publisher, only with a `PUBLISH:` line) -> `index-refresh` (default on, `no-index` off; also run as a script after each wave merge, failure only warns) -> `live-check` (ui); FAIL -> ask_main.
State: .work/<stage>/orch.jsonl (append-only events).
"""

import argparse
import datetime
import os
import re
import subprocess
import sys

from . import gitx, lead_context, worktree
from .briefs import leads_for
from .configuration import (
    ADDENDUM_HEADING,
    ADDENDUM_TIME_FORMAT,
    AUDITOR_REMIT,
    AUDITORS,
    BLOCKED,
    CLEAN,
    DONE,
    ENV_NO_INDEX,
    EV_DISPATCH,
    EV_REPORT,
    EV_RETRY,
    FIXES,
    METRICS_LOG,
    WORK_DIR,
    project,
    test_env,
    tool_cmd,
)
from .graph import decide
from .plan_files import is_last_phase
from .requests import take_request
from .triggers import is_trigger, log_trigger, retro_signals, trigger_log
from .util import TOOLS, add_subcommand, append_event, append_jsonl, jprint, now, paths, read_jsonl, read_text, stage_root, work_path, write_text
from .verdicts import ROLES, missing_note, verdict


def cmd_triggers(a):
    """One line per trigger: when, stage, step, agent model/effort, outcome, important?, why."""
    recs = read_jsonl(trigger_log(a))
    out = {(r["stage"], r["id"]): r["verdict"] for r in recs if r["ev"] == "outcome"}
    for r in recs:
        if r["ev"] != "trigger" or (a.stage and r["stage"] != a.stage):
            continue
        v = out.get((r["stage"], r["id"]), "pending")
        imp = "yes" if v in ("FIXES", "MISSING", "BLOCKED", "FAIL") else "no" if v != "pending" else "?"
        print(
            f"{r['ts']} {r['stage']} {r['id']} {r['step'] or 'request'} {r['agent']} {r.get('model') or 'default'}/{r.get('effort') or 'default'} -> {v} important={imp} | {r['why']}"
        )


def _incident_lines(a):
    """Retro: suspected reviewer misses (red final-test, merge-fix, blocked pr-ready) matched to the task patches `wt.py merge` saved. Never fails the retro."""
    try:
        if TOOLS not in sys.path:
            sys.path.insert(0, TOOLS)
        from retro import incidents

        return "\n".join(incidents.render(incidents.scan(os.path.abspath(getattr(a, "dir", ".")), a.stage), a.stage))
    except Exception as e:
        return f"INCIDENTS: scan failed ({e})"


def cmd_retro(a):
    sig = retro_signals(a)
    print("\n".join(f"RETRO {n}: {t}" for n, t in sig) if sig else "RETRO: no log signals tripped")
    rn = work_path(paths(a)[0], "retro-numbers.md")
    print(
        f"Transcript numbers (context, turns, cost): {rn}"
        if os.path.exists(rn)
        else f"No {rn} (flag no-retro, or the stage is not finished): run retro/retro_numbers.py --orch .work/{a.stage}/orch.jsonl yourself."
    )
    print(_incident_lines(a))
    print(f"Write .work/{a.stage}/retro/retro.md (<= 15 lines: tripped signals with numbers, one-line cause each, the pipeline change made in the same pass).")


def warm_index(a):
    """Keep the code search index fresh without anyone waiting for it: at most once a minute, start `local.py index` for the project in the background.
    Cheap when nothing changed (about a second); after a merge or on a new worktree only the changed files are re-read, and vectors come from the shared cache.
    ORCH_NO_INDEX=1 turns it off."""
    tool = os.path.join(TOOLS, "search", "local.py")
    w, _ = paths(a)
    stamp = os.path.join(w, ".index-warm")
    try:
        if os.environ.get(ENV_NO_INDEX) or not os.path.exists(tool) or not os.path.isdir(a.dir):
            return
        if os.path.exists(stamp) and now() - os.path.getmtime(stamp) < 60:
            return
        os.makedirs(w, exist_ok=True)
        open(stamp, "w").close()
        subprocess.Popen(
            [sys.executable, "-I", tool, "index", "--root", a.dir],
            stdin=subprocess.DEVNULL,
            stdout=open(os.path.join(w, "index.log"), "ab"),  # noqa: SIM115  # handle outlives this function
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        pass  # a missing index only makes search slower, never blocks the stage


def _unreported(evs):
    """Ids of dispatches that got neither a report nor a later retry/skip of the same task."""
    reported = {e["id"] for e in evs if e["ev"] == EV_REPORT}

    def cleared(i, e):  # a later retry/skip of the same task settles the dispatch
        return any(x["ev"] in ("retry", "skip") and x.get("task") == e.get("task") for x in evs[i + 1 :])

    return [e["id"] for i, e in enumerate(evs) if e["ev"] == EV_DISPATCH and e["id"] not in reported and not cleared(i, e)]


def _record_dispatch(a, log, evs, d):
    """Log one dispatch (and its trigger); the lead's note from `retry --note` goes in front of the re-run agent's prompt."""
    note = next((e.get("note") for e in reversed([e for e in evs if e.get("task") == d["task"] and e["ev"] == EV_RETRY])), "")
    if note and d["agent"] != "script":
        d["prompt"] = f"Lead note (read first): {note}\n{d['prompt']}"
    trig = is_trigger(d)
    append_event(
        log,
        {
            "ev": "dispatch",
            "id": d["id"],
            "task": d["task"],
            "role": d["role"],
            "agent": d["agent"],
            "model": d["model"],
            "trig": trig,
            "free": bool(d.get("free")),
            "head": d.get("head") or "",
            "why": d.get("why") or why_from(evs, d),
            **({"tasks": d["tasks"]} if d.get("tasks") else {}),  # a late merge batch: the tasks it covers
        },
    )
    if trig:
        log_trigger(
            a,
            {
                "ev": "trigger",
                "id": d["id"],
                "step": d["task"],
                "agent": d["agent"],
                "model": d["model"],
                "effort": d.get("effort"),
                "why": d.get("why", ""),
            },
        )


def _dispatch_action(a, log, evs, act):
    for d in act["dispatches"]:
        _record_dispatch(a, log, evs, d)
    return {
        "action": "dispatch",
        "dispatches": [
            {k: v for k, v in d.items() if k not in ("task", "role", "why", "free", "head", "tasks") and not (k == "effort" and v is None)}
            for d in act["dispatches"]
        ],
    }


def _retro_suffix(a):
    """-> text to append to the final message when retro signals tripped, else ''."""
    if os.path.exists(work_path(paths(a)[0], "retro.md")):  # retro already written: never ask again
        return ""
    sig = retro_signals(a)
    if not sig:
        return ""
    seen: dict = {}
    for n, t in sig:
        seen.setdefault(n, []).append(t)
    why = "; ".join(f"{n}" + (f" x{len(ts)}" if len(ts) > 1 else "") + f" - {ts[0][:90]}" for n, ts in seen.items())  # each trigger once, with its reason
    return f"; retro needed [{why}]: {_retro_step(a)}"


def _retro_step(a):
    """-> the retro step the lead runs: the whole feature for its last phase (feature_retro.py reads every phase), else this stage's retro."""
    w, _ = paths(a)
    if is_last_phase(w):
        feature = os.path.dirname(os.path.dirname(a.stage))
        return f"run `{tool_cmd('retro/feature_retro.py')} --feature-dir {WORK_DIR}/{feature}` and write {WORK_DIR}/{feature}/retro.md (last phase: the whole feature)"
    return f"run `orch.py retro --stage {a.stage}` and write .work/{a.stage}/retro/retro.md"


def _log_ask(log, evs, message):
    """Record an `ask` event unless the last event is the same ask: a lead calling `next` again before changing anything is not a new question (retro counts asks)."""
    last = evs[-1] if evs else {}
    if last.get("ev") == "ask" and last.get("message") == message:
        return
    append_event(log, {"ev": "ask", "message": message})


def cmd_next(a):
    warm_index(a)
    w, log = paths(a)
    evs = read_jsonl(log)
    pending = _unreported(evs)
    if pending:
        msg = f"dispatch {pending[0]} was never reported; run `orch.py report --id {pending[0]}`"
        _log_ask(log, evs, msg)
        jprint({"action": "ask_main", "message": msg})
        return
    act = take_request(a, w, log)
    if act and act["action"] == "dispatch":
        act = {"action": "dispatch", "dispatches": [act]}
    act = act or decide(a)
    if act["action"] == "dispatch":
        act = _dispatch_action(a, log, evs, act)
    elif act["action"] == "ask_main":
        _log_ask(log, evs, act["message"][:80])
    elif act["action"] == "done":
        write_text(os.path.join(paths(a)[0], "chain.md"), chain_text(evs))
        act["message"] += _retro_suffix(a)
    jprint(act)


def why_from(evs, d):
    """One-line reason a step runs now: the last event of the same task that led here."""
    prev = [e for e in evs if e.get("task") == d["task"] and e["ev"] in ("report", "retry", "skip")]
    if not prev:
        return "first run of this step"
    e = prev[-1]
    if e["ev"] == EV_REPORT:
        return f"after {e.get('role')} {e.get('verdict')}" + (f" ({e['reason']})" if e.get("reason") else "")
    return f"{e['ev']} by lead" + (f": {e['note'][:80]}" if e.get("note") else "")


def chain_text(evs):
    """Log of every agent call in order: number, id, agent/model, task, reason, verdict."""
    out: list = []
    verdicts = {e["id"]: e.get("verdict") for e in evs if e["ev"] == EV_REPORT}
    for e in (x for x in evs if x["ev"] == EV_DISPATCH):
        out.append(
            f"{len(out) + 1}. {e['id']} {e['agent']}/{e.get('model') or 'default'} {e.get('task')}: {e.get('why') or '-'} -> {verdicts.get(e['id'], 'no report')}"
        )
    return "\n".join(out) + "\n"


def cmd_leads(a):
    """For the lead's briefs (planning architect, any agent): mandatory first search, paste the output into the brief."""
    print(leads_for(a, a.key, a.text).strip())


def cmd_chain(a):
    w, log = paths(a)
    text = chain_text(read_jsonl(log))
    write_text(os.path.join(w, "chain.md"), text)
    print(text, end="")


def _report_verdict(d, text):
    if d["role"] not in ROLES:
        return ("REPORTED", "") if text.strip() else ("MISSING", "no report file written")
    if not text.strip():
        return ROLES[d["role"]][1], "no report file written"
    return verdict(d["role"], text)


WHY_MAX = 300
_WHY_LINE = re.compile(r"^(?:VERDICT:\s*)?(?:BLOCKED|ESCALATE|FAIL|PASS|DONE|OK)\b:?\s*(.*)$")
_HANDOFF_LINE = re.compile(r"^\W*REPORT\s+\S+\W*$")  # the agent's last reply line `REPORT <id>`: a handoff, never a reason


def _report_why(text):
    """First line that says why: verdict-line text, else the first non-verdict line; a bare verdict word when the report says no more. Skips the `REPORT <id>` handoff line. Cut to WHY_MAX chars (a block reason names the file, command or branch; 80 cut it)."""
    verdict_word = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _HANDOFF_LINE.match(line):
            continue
        m = _WHY_LINE.match(line)
        if m:
            if m.group(1).strip():
                return m.group(1).strip()[:WHY_MAX]
            verdict_word = verdict_word or re.sub(r"^VERDICT:\s*", "", line).split()[0].strip("*:.")
            continue
        return line[:WHY_MAX]
    return verdict_word[:WHY_MAX]


def _auto_retry(log, evs, d, reason):
    """First unclear verdict of a role: run the agent once more. -> True when a retry was logged."""
    if (
        d["role"] not in ROLES
        or reason not in ("no clear verdict", "no report file written")
        or any(e["ev"] == EV_RETRY and e.get("auto") and e.get("task") == d["task"] and e.get("role") == d["role"] for e in evs)
    ):
        return False
    append_event(
        log,
        {
            "ev": "retry",
            "task": d["task"],
            "auto": True,
            "role": d["role"],
            "note": missing_note(d["role"], reason),
            **({"from": "review"} if d["role"] == "reviewer" else {}),
        },
    )
    # a clean retry, no dev try consumed (the retry resets the task's history); the reason is kept for the retro
    append_jsonl(
        os.path.join(os.path.dirname(log), METRICS_LOG),
        {"ev": "auto_retry", "ts": now(), "id": d["id"], "task": d["task"], "role": d["role"], "reason": reason},
    )
    return True


def _materialize_audit(w, d, v, body):
    """Auditors have no Write tool: copy the report to audits/<step>.md when a FIXES/CLEAN audit left no such file. Never overwrites."""
    if d["role"] not in AUDITORS or v not in (CLEAN, FIXES) or not d["task"].startswith("audit-"):
        return
    dest = work_path(w, f"{d['task']}.md", create=True)
    if not os.path.exists(dest):
        write_text(dest, body)


def _remit_verdict(a, d, v, body):
    """-> (verdict, ignored lines). An auditor's FIXES caused only by FAIL lines on Plan.md checklist items outside its remit becomes CLEAN: those findings are logged as ignored, not fixed."""
    from .audit_phases import checklist_items, out_of_remit_split, plan_rel  # lazy: audit_phases imports this package's graph

    remit = project(a.dir).get("auditor_remit", AUDITOR_REMIT)
    if d["role"] not in remit or v != FIXES:
        return v, []
    items = checklist_items(read_text(os.path.join(a.dir, plan_rel(a.stage))))
    ignored, severe = out_of_remit_split(body, items, remit[d["role"]]) if items else ([], [])
    return (CLEAN, ignored) if ignored and not severe else (v, [])


_VERDICT_LINE = re.compile(r"^\s*VERDICT\s*:", re.M)


def _worktree_report(a, d):
    """-> the job worktree's report file of dispatch d (the dev wrote it there, not in the main checkout) when it exists, else None."""
    if not (a.dir and d.get("task")):  # no job worktree without a project dir or a task (non-dev dispatches)
        return None
    root = worktree.layout(a.dir, a.stage, d["task"])[2]
    f = work_path(os.path.join(root, WORK_DIR, a.stage), f"report-{a.id}.md")
    return f if os.path.isfile(f) else None


def cmd_report(a):
    w, log = paths(a)
    evs = read_jsonl(log)
    d = [e for e in evs if e["ev"] == EV_DISPATCH and e["id"] == a.id]
    if not d:
        sys.exit(f"orch: unknown dispatch id {a.id}")
    d = d[0]
    f = work_path(w, f"report-{a.id}.md", create=True)
    if not _VERDICT_LINE.search(read_text(f)):  # the main report has no VERDICT line: take the job worktree's report when it has one
        wt_f = _worktree_report(a, d)
        if wt_f and _VERDICT_LINE.search(read_text(wt_f)):
            write_text(f, read_text(wt_f))
    if a.text and not read_text(f).strip():  # --text only fills a missing/blank file
        write_text(f, a.text)
    body = read_text(f)
    v, reason = _report_verdict(d, body)
    v, ignored = _remit_verdict(a, d, v, body)
    if ignored:
        reason = f"ignored {len(ignored)} finding(s) outside the {d['role']} remit: " + "; ".join(x[:WHY_MAX] for x in ignored)
        append_event(log, {"ev": "ignored", "id": a.id, "task": d["task"], "role": d["role"], "findings": ignored})
    _materialize_audit(w, d, v, body)
    append_event(log, {"ev": "report", "id": a.id, "task": d["task"], "role": d["role"], "verdict": v, "reason": reason, "why": _report_why(body), "file": f})
    retried = _auto_retry(log, evs, d, reason)
    if d.get("trig"):
        log_trigger(a, {"ev": "outcome", "id": a.id, "step": d["task"], "agent": d["agent"], "verdict": v, "report": f})
    jprint({"recorded": a.id, "verdict": v, "why": _report_why(body), **({"retry": True} if retried else {})})


def _checks_green(a):
    """-> (note, gate command count) when the task's Test lines and the gates are green in its worktree, else None."""
    import testrun  # lazy: imports wt, which imports this package
    from audit import final_check

    v, note = testrun.run(a.stage, a.step, os.path.abspath(a.dir))
    cwd = testrun.workdir(a.dir, a.stage, a.step)
    if v != "DONE" or "skipped" in note or not cwd:
        return None
    cfg = project(a.dir)
    cmds = final_check.commands(cfg, cwd)
    results = final_check.run_all(cmds, cwd, {**os.environ, **test_env(cfg, a.dir)}, groups=final_check.gate_groups(cfg, cwd)) if cmds else []
    return None if any(r[1] for r in results) else (note, len(cmds))


_OWNER_WORDS = re.compile(r"owner|decision|decide|scope|unsafe|security|permission|not allowed|forbidden|ambiguous|unclear|which of", re.I)
_CHECK_WORDS = re.compile(r"\btests?\b|environment|\benv\b|sandbox|network|install|timeout|timed out|toolchain|missing (?:tool|binary)|flaky", re.I)


def _blocked_for_checks(rep):
    """True when the BLOCKED reason is about tests or the environment, not an owner decision, scope or safety question."""
    text = " ".join(str(rep.get(k) or "") for k in ("reason", "why")) + " " + read_text(rep.get("file") or "")
    return bool(_CHECK_WORDS.search(text)) and not _OWNER_WORDS.search(text)


def _job_has_commits(a):
    """True when the task's job branch holds commits its base branch lacks (a blocked dev with nothing committed has nothing to call done)."""
    info = worktree.live_job(a.dir, a.stage, a.step)
    if not info:
        return False
    r = gitx.try_out(info["root"], "rev-list", "--count", f"{info['base']}..{info['branch']}")
    return bool(r) and str(r).strip().isdigit() and int(str(r).strip()) > 0


def _blocked_dev_is_green(a, log):
    """retry of a task whose last dev report is BLOCKED: run its Test lines and the gates in its worktree. -> True when both are green and `done` is recorded (no model call)."""
    evs = read_jsonl(log)
    reps = [e for e in evs if e.get("task") == a.step and e["ev"] == EV_REPORT and e.get("role") == "dev"]
    if not reps or reps[-1]["verdict"] != BLOCKED or not os.path.isfile(os.path.join(a.dir, WORK_DIR, a.stage, f"{a.step}.md")):
        return False
    if not _blocked_for_checks(reps[-1]) or not _job_has_commits(a):
        return False
    ok = _checks_green(a)
    if not ok:
        return False
    note, n = ok
    nums = [int(m.group(1)) for e in evs if (m := re.fullmatch(r"retry-(\d+)", str(e.get("id", ""))))]
    rid = f"retry-{max(nums, default=0) + 1}"
    f = work_path(paths(a)[0], f"report-{rid}.md", create=True)
    write_text(f, f"VERDICT: DONE\nretry check, no model call: {note}; {n} gate command(s) green.\n")
    append_event(
        log, {"ev": "report", "id": rid, "task": a.step, "role": "dev", "verdict": "DONE", "reason": "retry check green", "why": note[:WHY_MAX], "file": f}
    )
    jprint({"recorded": "done", "step": a.step, "log": log, "why": "Test lines and gates green: no model call"})
    return True


def _reset_fix_chain(log, a):
    """retry of merge-w{N}: its fix-gate devs and re-merges start fresh too, so the next red merge is not judged by the old g1/g2 reports."""
    m = re.fullmatch(r"merge-w(\d+)(-late\d*|-p\d+)?", a.step)  # also a late or partial merge: the same ladder under its suffix
    if not m:
        return
    n, tag = m.group(1), m.group(2) or ""
    ladder = [f"fix-gate-w{n}{tag}", f"fix-gate-w{n}{tag}-2", f"merge-w{n}{tag}-g1", f"merge-w{n}{tag}-g2"]
    if tag:  # a late or partial batch is finished once ANY step of its ladder is DONE, so the merger fix and the re-merge must restart too
        ladder += [f"merge-fix-w{n}{tag}", f"merge-w{n}{tag}-2"]
    for sid in ladder:
        append_event(log, {"ev": "retry", "task": sid, "note": f"retry of {a.step}"})


def _refresh_job(a, log):
    """retry: rebase the job branch onto the stage branch; drop uncommitted leftovers of the dead dev (logged, commits kept). A conflict stops the retry."""
    try:
        res = worktree.refresh(a.dir, a.stage, a.step, discard=True)
    except RuntimeError as e:
        sys.exit(f"orch: retry {a.step} BLOCKED: {e}")
    if res["rebased"] or res["discarded"]:
        append_event(log, {"ev": "retry_refresh", "task": a.step, **res})
        jprint({"refreshed": a.step, **res})


def _write_addendum(a):
    """retry --addendum: append one dated block to the step's task file (same path briefs.prompt_for names). A step without one (script steps) is refused."""
    path = os.path.join(os.path.abspath(a.dir), WORK_DIR, a.stage, f"{a.step}.md")
    if not os.path.isfile(path):
        sys.exit(f"orch: retry --addendum refused: {a.step} has no task file {path} (script steps take --note only)")
    when = datetime.datetime.now(datetime.UTC).strftime(ADDENDUM_TIME_FORMAT)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n{ADDENDUM_HEADING.format(when=when)}\n\n{a.addendum.strip()}\n")


def _skip_guard(a):
    """skip: a skipped task never reaches the work branch: refuse while its job holds work the work branch lacks."""
    import wt  # lazy: wt imports this package

    why = wt.skip_blocker(a.dir, a.stage, a.step)
    if why:
        sys.exit(
            f"orch: skip {a.step} refused: its job branch has work the stage branch lacks ({why}). "
            f"Run `{tool_cmd('wt.py')} merge --stage {a.stage} --task {a.step} --dir {a.dir}` first to keep it, or `{tool_cmd('wt.py')} remove --force --stage {a.stage} --task {a.step} --dir {a.dir}` to discard it, then skip again."
        )


def _apply_addendum(a):
    """retry --addendum: write the block to the task file, then fold the addendum into the note the dev reads first."""
    _write_addendum(a)
    a.note = "\n".join(x for x in (a.note, a.addendum) if x)


def _unmerged_prereqs(a, evs):
    """retry: the other tasks the step's task file or note names (`task-N`) that are DONE (a dev DONE report) with a job branch the stage branch does not hold yet. A retry starts from the stage branch, so it would lack them. Advisory: any error gives []. -> [task ids]."""
    path = os.path.join(a.dir, WORK_DIR, a.stage, f"{a.step}.md")
    if not os.path.isfile(path):
        return []
    try:
        text = read_text(path) + "\n" + (getattr(a, "note", "") or "")
    except (OSError, ValueError):
        return []
    named = sorted({f"task-{n}" for n in re.findall(r"\btask-(\d+)\b", text)} - {a.step}, key=lambda t: int(t[5:]))
    done = {e.get("task") for e in evs if e["ev"] == EV_REPORT and e.get("role") == "dev" and e.get("verdict") == DONE}
    try:
        return [t for t in named if t in done and worktree.live_job(a.dir, a.stage, t) and not worktree.is_merged(a.dir, a.stage, t)]
    except (RuntimeError, OSError):
        return []


def _prereq_warning(a, ids):
    """The text of the retry warning for done-but-unmerged prerequisite tasks IDS."""
    return (
        f"{a.step} names {', '.join(ids)}, whose finished work is not merged into the stage branch yet: the retry would start from a base without it. "
        f"The driver merges a done task of an earlier wave before the next wave starts; merge now with `{tool_cmd('wt.py')} merge --stage {a.stage} --task <task> --dir {a.dir}` "
        f"or add the prerequisite to the task's `Depends:` line so it is held until merged."
    )


def cmd_reset(a):
    """retry: forget a step's history so it runs again. skip: count the step as satisfied (the owner decided)."""
    root = stage_root(a.dir, a.stage)
    if root is None:  # a typo or a wrong cwd must not create a fresh .work/<stage>/orch.jsonl somewhere
        sys.exit(f"orch: {a.cmd} refused: no .work/{a.stage} in {os.path.abspath(a.dir)} or any parent folder (cwd {os.getcwd()}); pass --dir <stage root>")
    a.dir = root
    _w, log = paths(a)
    if a.cmd == "skip":
        _skip_guard(a)
    if a.cmd == "retry" and getattr(a, "addendum", ""):  # before any other write: a refused script step changes nothing
        _apply_addendum(a)
    if a.cmd == "retry" and not getattr(a, "from_step", "") and _blocked_dev_is_green(a, log):
        return
    warn = _unmerged_prereqs(a, read_jsonl(log)) if a.cmd == "retry" else []  # before the retry writes anything
    if a.cmd == "retry":
        _refresh_job(a, log)
    ev = {"ev": a.cmd, "task": a.step, "note": a.note, **({"from": "review", "role": "reviewer"} if getattr(a, "from_step", "") == "review" else {})}
    if a.cmd == "retry":
        _reset_fix_chain(log, a)
    append_event(log, ev)
    last = (read_jsonl(log) or [{}])[-1]
    if (last.get("ev"), last.get("task")) != (a.cmd, a.step):  # a silent miss made the driver report the old BLOCKED again
        sys.exit(f"orch: {a.cmd} for {a.step} was not written to {log}")
    jprint(
        {
            "recorded": a.cmd,
            "step": a.step,
            "log": log,
            "next": "the driver has exited: stop the old Monitor (TaskStop), then relaunch the driver; old BLOCKED lines are stale",
            **({"warn": _prereq_warning(a, warn)} if warn else {}),
        }
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (
        ("next", cmd_next),
        ("report", cmd_report),
        ("retry", cmd_reset),
        ("skip", cmd_reset),
        ("triggers", cmd_triggers),
        ("retro", cmd_retro),
        ("chain", cmd_chain),
        ("leads", cmd_leads),
    ):
        p = add_subcommand(sub, name, fn, stage_required=(name != "triggers"))
        if name == "leads":
            p.add_argument("--key", default="plan")
            p.add_argument("--text", required=True)
        if name == "report":
            p.add_argument("--id", required=True)
            p.add_argument("--text", default="")
        if name in ("retry", "skip"):
            p.add_argument("--step", required=True)
            p.add_argument("--note", default="")
            if name == "retry":
                p.add_argument("--from", dest="from_step", default="", choices=["", "review"])
                p.add_argument("--addendum", default="")
    a = ap.parse_args(argv)
    lead_context.check()
    a.fn(a)
