"""Audit machinery: the security-audit trigger, the audit steps and rounds, and the bounded fix cycle."""

import fnmatch
import os
import re
import shlex
import subprocess
from collections.abc import Iterator

from . import gitx, plan_files
from .briefs import NAV, leads_for, read_head, tail, ux_flows_note
from .configuration import (
    ACCEPTANCE_FILE,
    AUDIT_ESCALATE_SUFFIX,
    AUDITOR_ESCALATE,
    AUDITOR_ESCALATE_MODEL,
    AUDITOR_REMIT,
    AUDITOR_START_MODEL,
    BLOCKED,
    CLEAN,
    DOCS_WRITES,
    DONE,
    ESCALATE,
    EV_REPORT,
    FIXES,
    FRESHNESS_AUDITOR,
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    PLAN_CONTEXT_FILE,
    PLAN_FILE,
    PRACTICES_DIR,
    REMIT_MATCH_CHARS,
    REQUEST_FILE,
    SEC_AUDIT_CONTENT,
    SEC_AUDIT_PATHS,
    SEC_AUDIT_WORDS,
    SEC_CODE_EXTS,
    SEVERE_PATTERN,
    STALE_REPORT_NOTE,
    STALE_RETRY_STEP,
    TASKS_FILE,
    WIRING_DIRS_KEY,
    WORK_DIR,
    allowed,
    tool_cmd,
    work_branch,
)
from .entry import name_of_stage
from .phase_context import brief_digest
from .steps import history, last_verdict, step
from .util import read_text, work_path, write_text


def _wb(a, cfg):
    """The work branch of this run: derived from the feature name, else the stage name."""
    return work_branch(cfg, getattr(a, "feature", None) or a.stage)


def audit_base(a, cfg) -> str:
    """Base ref for a stage's audit context and recall: the commit `branch_work` had when a feature phase started (`.work/<S>/base.txt`, written by cli_feature), so phase N does not count the files of phases 1..N-1; else `branch_release`."""
    f = os.path.join(a.dir, WORK_DIR, a.stage, "base.txt")
    ref = read_text(f).strip() if os.path.isfile(f) else ""
    return ref or cfg["branch_release"]


def block_args(a, cfg, audit_dir) -> str:
    """CLI args for audit_context.py: the plan-vs-changes file and the changed paths of the block (base..work branch), so only their rows are inlined; "" when git gives no paths."""
    names = gitx.try_text(audit_dir, "-c", "core.quotepath=off", "diff", "-M", "--name-only", f"{audit_base(a, cfg)}...{_wb(a, cfg)}").splitlines()
    names = [n for n in names if n and "," not in n]
    if not names:
        return ""
    return f" --plan-vs-changes .work/{a.stage}/audits/plan-vs-changes.md --block {shlex.quote(','.join(names))}"


def _is_test_path(f):
    parts = f.split("/")
    base = parts[-1]
    return any(x in ("test", "tests", "__tests__", "spec") for x in parts[:-1]) or bool(re.match(r"(test_|.*_test\.|.*\.test\.|.*\.spec\.|conftest)", base))


def _is_code_path(f):
    return f.endswith(SEC_CODE_EXTS) and not _is_test_path(f)


def sec_path_hit(path, pats) -> bool:
    """True when PATH (case-insensitive) matches a glob of PATS; with the default list also when a word of SEC_AUDIT_WORDS is a word of the basename."""
    low = path.lower()
    if any(fnmatch.fnmatch(low, str(p).lower()) for p in pats):
        return True
    if list(pats) == SEC_AUDIT_PATHS:
        return bool(set(re.split(r"[^a-z0-9]+", low.rsplit("/", 1)[-1])) & set(SEC_AUDIT_WORDS))
    return False


def sec_diff_hit(diff) -> bool:
    """True when a unified -U0 DIFF (with -M) adds a line matching SEC_AUDIT_CONTENT in a code file, or deletes any line of a non-test code file (a removed check or guard). Pure renames have no hunks."""
    cur, in_hunk = "", False
    for ln in diff.splitlines():
        if ln.startswith("diff --git "):
            in_hunk = False
            cur = ln.rsplit(" b/", 1)[-1].strip('"')
        elif not in_hunk and ln.startswith("@@"):
            in_hunk = True
        elif not in_hunk and ln.startswith("+++ ") and ln[4:] != "/dev/null":
            cur = ln[4:].removeprefix("b/").strip('"')
        elif in_hunk and _is_code_path(cur.lower()):
            if ln.startswith("-"):
                return True
            if ln.startswith("+") and re.search(SEC_AUDIT_CONTENT, ln[1:], re.I):
                return True
    return False


def sec_audit_needed(a, cfg, audit_dir) -> bool:
    """The Opus sec audit runs only when the diff base..work branch touches a security path (`sec_audit_paths`, fnmatch, case-insensitive; default configuration.SEC_AUDIT_PATHS)
    or sec_diff_hit (a risky added line, or any deleted line, in a code file). `["*"]` always runs. An unreadable or empty diff keeps the audit."""
    pats = cfg.get("sec_audit_paths")
    if not isinstance(pats, list) or not pats:
        pats = SEC_AUDIT_PATHS
    if "*" in pats:
        return True
    rng = f"{audit_base(a, cfg)}...{_wb(a, cfg)}"
    try:
        r = gitx.run(audit_dir, "-c", "core.quotepath=off", "diff", "-M", "--name-only", rng)
        files = r.stdout.splitlines()
        if r.returncode or not files:
            return True
        if any(sec_path_hit(f, pats) for f in files):
            return True
        diff = gitx.run(audit_dir, "-c", "core.quotepath=off", "diff", "-M", "-U0", rng).stdout
    except subprocess.TimeoutExpired:  # an unreadable diff keeps the audit
        return True
    return sec_diff_hit(diff)


ANGLE = {
    "sec-auditor": "untrusted input, escaping into generated HTML, parsing, path handling, secrets",
    "arch-auditor": "module boundaries, duplication, layering, dead code",
    "ux-reviewer": "rendering, layout, keyboard and touch handling",
}


def last_report(evs, sid) -> str:
    """Path of the latest report of a step, "" when none."""
    return next((e["file"] for e in reversed(history(evs, sid)) if e["ev"] == EV_REPORT), "")


def fix_step(a, tasks, sid, reports, what, key, why) -> dict:
    """Architect step that turns a failed check into fix tasks: appended to TASKS.md with a later `wave K:` line, then run by the normal waves (dev -> reviewer -> merger)."""
    S = a.stage
    last_wave = max(t["wave"] for t in tasks)
    stage_dir = os.path.join(a.dir, WORK_DIR, S)
    req = read_text(os.path.join(stage_dir, REQUEST_FILE))
    digest = f" Digest of the request and the plan context (1 KB): {brief_digest(req, read_text(os.path.join(stage_dir, PLAN_CONTEXT_FILE)))}" if req else ""
    return step(
        sid,
        "architect",
        None,
        f"Stage {S}: {what} Reports: {', '.join(reports)}. Split what is wrong into fix tasks exactly as you plan a stage: append the entries "
        f"to .work/{S}/TASKS.md in the format it already uses (task numbers after the last existing one, the driver chooses models, every task starts on Haiku), write the task-N.md files, and add ONE line "
        f"`wave {last_wave + 1}: task-a, task-b` (parallel only when they share no paths). Do not touch existing rows. Skip nits. "
        f"If the report shows the tool or environment failed (browser did not start, server unreachable, toolchain missing) and not a defect in the change, plan nothing and reply BLOCKED."
        + digest
        + STALE_REPORT_NOTE.format(retry=f"{tool_cmd('orch.py')} retry {name_of_stage(S)} --step {STALE_RETRY_STEP}")
        + NAV
        + leads_for(a, key, " ".join(read_head(a, os.path.basename(r), 500) for r in reports))
        + tail(a, "{rid}", allowed(DONE, BLOCKED), "a 3-line summary naming the new tasks"),
        {DONE},
        None,
        why,
    )


def audit_report_name(task) -> str | None:
    """-> the report file name of audit step TASK (`audit-sec-2`, `audit-arch-opus` -> audit-arch.md; the Opus rerun writes the first pass file), or None for a step that is no audit."""
    if not task or not task.startswith("audit-"):
        return None
    return task.removesuffix(AUDIT_ESCALATE_SUFFIX) + ".md"


def save_audit_reply(stage_dir, task, reply) -> str | None:
    """Save the auditor's reply as `audits/<report>` when the auditor left that file missing or empty, so a re-audit can read it. -> the path written, else None."""
    name = audit_report_name(task)
    if not name or not (reply or "").strip():
        return None
    path = work_path(stage_dir, name, create=True)
    if read_text(path).strip():
        return None
    write_text(path, reply)
    return path


def audit_dir_of(a, cfg) -> str:
    cands = (
        ([os.path.join(a.dir, cfg["repo"])] if cfg["repo"] else [])
        + [a.dir]
        + [os.path.join(a.dir, d) for d in sorted(os.listdir(a.dir)) if os.path.isdir(os.path.join(a.dir, d, ".git"))]
    )  # configured repo, else the run dir, else the first repo below it (orch runs from the workspace root)
    return next((d for d in cands if read_text(os.path.join(d, cfg["audit_file"]))), a.dir)


def _audit_effort_model(flags, ag, suffix, small):
    """-> (effort, model) of one audit step."""
    if ag == FRESHNESS_AUDITOR:  # docs check: always Haiku, whatever the effort flags
        return "low", MODEL_HAIKU
    cheap = bool(suffix) and ag != "ux-reviewer" and small
    effort = (
        ("low" if suffix else None) if ag == "ux-reviewer" else "low" if cheap else flags["effort"]
    )  # sec/arch effort: owner/lead flag, default = frontmatter (medium)
    model = (
        {"": MODEL_SONNET, "-2": MODEL_HAIKU, "-3": MODEL_HAIKU}[suffix]
        if ag == "ux-reviewer"
        else MODEL_HAIKU
        if cheap
        else MODEL_OPUS
        if effort == "high"
        else AUDITOR_START_MODEL.get(ag, MODEL_SONNET)
    )  # ux: Sonnet first pass, Haiku re-check; sec and arch: Sonnet, Opus on `audit-high` or after their ESCALATE (`_escalated_steps`)
    return effort, model


def checklist_items(text) -> list[str]:
    """-> the `- ` item lines of the `## Acceptance checklist` section of a Plan.md text ([] when there is no section)."""
    head = "## Acceptance checklist"
    if head not in text:
        return []
    body = text.split(head, 1)[1].split("\n## ", 1)[0]
    return [ln.strip() for ln in body.splitlines() if ln.lstrip().startswith("- ")]


def remit_items(items, topics) -> list[str]:
    """-> the checklist items that touch one of the remit `topics` (case-insensitive substring)."""
    return [it for it in items if any(t.lower() in it.lower() for t in topics)]


def plan_rel(stage) -> str:
    """-> the Plan.md path (below the run dir) of a stage; stage `F/phases/NN-x` uses `.work/F/Plan.md`."""
    parts = stage.split("/")
    return os.path.join(WORK_DIR, parts[0], PLAN_FILE) if len(parts) >= 3 and parts[1] == "phases" else os.path.join(WORK_DIR, stage, PLAN_FILE)


_SEVERE_RE = re.compile(SEVERE_PATTERN, re.I)


def out_of_remit_split(report, items, topics) -> tuple[list[str], list[str]]:
    """-> (ignored, severe). `ignored`: report lines that name a checklist item outside the remit `topics`; `severe`: the other lines that still say FAIL / blocker / should-fix."""
    outside = [it.lstrip("- ").lower()[:REMIT_MATCH_CHARS] for it in items if it not in remit_items(items, topics)]
    ignored, severe = [], []
    for ln in report.splitlines():
        if any(o and o in ln.lower() for o in outside):
            if _SEVERE_RE.search(ln):
                ignored.append(ln.strip())
        elif _SEVERE_RE.search(ln):
            severe.append(ln.strip())
    return ignored, severe


def checklist_clause(a, cfg, ag) -> str:
    """Audit-brief sentence on the plan's `## Acceptance checklist` ("" when the stage has no Plan.md with one). The auditor `ag` gets only the items in its remit (`auditor_remit`); a role with no remit gets the whole checklist. Stage `F/phases/NN-x` uses `.work/F/Plan.md`."""
    rel = plan_rel(a.stage)
    items = checklist_items(read_text(os.path.join(a.dir, rel)))
    if not items:
        return ""
    s = f" The driver already ran the command items in a fresh worktree of the stage branch: read .work/{a.stage}/{ACCEPTANCE_FILE} for their result, never run them yourself."
    remit = cfg.get("auditor_remit", AUDITOR_REMIT)
    if ag not in remit:
        return f" Plan.md `## Acceptance checklist` ({rel}): report every item as PASS, FAIL or UNKNOWN with file:line evidence before your own findings." + s
    mine = remit_items(items, remit[ag])
    if not mine:
        return f" Plan.md `## Acceptance checklist` ({rel}) has no item in your remit: do not review it and do not raise findings outside your remit." + s
    return (
        f" Plan.md `## Acceptance checklist` ({rel}): only these items are in your remit, report each as PASS, FAIL or UNKNOWN with file:line evidence before your own findings; other items are not reviewed by you:\n"
        + "\n".join(mine)
        + s
    )


def _arch_clause(a, flags, ag, suffix):
    # arch starts together with sec and ux on the first pass: it judges the stage alone and does not read their reports
    return (
        " Judge the stage as a whole; the sec and ux audits run at the same time, do not wait for their reports." if ag == "arch-auditor" and not suffix else ""
    )


def _reaudit_clause(a, cfg, k, suffix):
    S = a.stage
    return "" + (
        f" This is a re-audit: review only the findings in .work/{S}/audits/{k}{'' if suffix == '-2' else '-' + str(int(suffix[1:]) - 1)}.md and the diff of the fix commits made since that audit (`git log`/`git diff` on `{_wb(a, cfg)}`). Do not rerun the full tool scans. Finish within about 15 turns."
        if suffix
        else ""
    )


def _audit_tail(a, ag, suffix, esc):
    verdicts = [CLEAN, FIXES, BLOCKED, ESCALATE] if ag in AUDITOR_ESCALATE and not suffix and not esc else [CLEAN, FIXES, BLOCKED]
    return tail(a, "{rid}", allowed(*verdicts), "a 3-line summary with the path of the full report")


def _freshness_clause(a, cfg, ag) -> str:
    """The freshness-auditor's task: the stage diff against the docs folder and the tests (report path is the generic `{k}.md` line)."""
    if ag != FRESHNESS_AUDITOR:
        return ""
    return (
        f" Diff to check: `git diff {audit_base(a, cfg)}...{_wb(a, cfg)}`; docs folder `{cfg.get('docs_dir')}`. Compare the changed code with the docs and the tests: "
        "report each doc statement the code now contradicts or omits, and each changed behaviour with no test. Edit nothing."
    )


def _audit_prompt(a, cfg, flags, has_audit_md, k, ag, suffix, effort, esc=False):
    S = a.stage
    return (
        f"Stage {S} audit ({ag}) of all stage changes on branch `{_wb(a, cfg)}`, per {PRACTICES_DIR}/audits.md. Full report to .work/{S}/audits/{k}{suffix}.md "
        f"(findings ranked blocker / should-fix / nit)."
        + (
            f" The tool run is done for you: read .audits/raw/*findings.json only through `{tool_cmd('audit/audit_report.py')} <findings.json> [--top N] [--tool X] [--group file|rule] [--list]` (`--list` shows each finding; unused-code claims carry a `[check: ...]` from `lp nav refs`, a hint not proof), do not rerun or grep the tools."
            if ag != "ux-reviewer" and has_audit_md
            else ""
        )
        + (
            f" The screenshots and layout numbers are done for you by script: read .work/{S}/ux-shots{'-2' if suffix else ''}/SUMMARY.md first, view every flagged screen, the key screens and every screen of a flow that covers a changed file; extend ux_shots.js or pass --steps yourself if the flows miss a screen."
            + ux_flows_note(a, cfg)
            + " A screen the stage changed that you could not see or check: do not write CLEAN. Put a line `UNVERIFIED: <screen>: <flow to add>` in your report and in the summary and answer VERDICT: FIXES (the fix round adds the flow, then the re-audit runs it)."
            if ag == "ux-reviewer"
            else ""
        )
        + (
            f" Deterministic scans (secrets, dependency audit) already ran by script: read .work/{S}/audits/scans.md instead of running those tools yourself."
            if ag in ("sec-auditor", "arch-auditor") and cfg.get("scans")
            else ""
        )
        + (
            f" Also read .work/{S}/audits/audit-context.md first when it exists: what the stage changed and who uses it, by script (lp nav; `text hits` are an unverified word search). If .work/{S}/audits/plan-vs-changes.md exists, it lists changed files no task covers and predicted paths nothing changed. If .work/{S}/plan-context.md exists, it holds the code leads the architect was given for this request; use it to judge whether the change matches the plan's intent."
            if ag in ("sec-auditor", "arch-auditor")
            else ""
        )
        + _freshness_clause(a, cfg, ag)
        + _arch_clause(a, flags, ag, suffix)
        + _reaudit_clause(a, cfg, k, suffix)
        + checklist_clause(a, cfg, ag)
        + " VERDICT: CLEAN = nothing to fix; FIXES = blocker or should-fix findings exist; BLOCKED = audit could not run (e.g. TOOL-REQUEST)."
        + (
            " ESCALATE = the turn cap or your depth ran out with areas of the remit still UNKNOWN: name them; the driver reruns the audit once on a stronger model."
            if ag in AUDITOR_ESCALATE and not suffix and not esc
            else ""
        )
        + (f" Effort is high because: {flags['why']}." if effort == "high" else "")
        + NAV
        + leads_for(a, k + suffix, ANGLE.get(ag, "") + " " + read_head(a, TASKS_FILE, 400))
        + _audit_tail(a, ag, suffix, esc)
    )


def _audit_step(a, cfg, flags, has_audit_md, small, k, ag, ok, suffix="", why="all base tasks done, merged and tested", esc=False):
    """One audit step. esc: the Opus rerun after the first pass said ESCALATE (id `k` + escalate suffix, same report file)."""
    effort, model = _audit_effort_model(flags, ag, suffix, small)
    if esc:
        model = AUDITOR_ESCALATE_MODEL
    return step(
        k + suffix + (AUDIT_ESCALATE_SUFFIX if esc else ""),
        ag,
        model,
        _audit_prompt(a, cfg, flags, has_audit_md, k, ag, suffix, effort, esc),
        ok,
        effort,
        why
        + (
            f"; effort high: {flags['why']}" if effort == "high" else "; effort low (small, non-risky stage)" if effort == "low" and ag != "ux-reviewer" else ""
        ),
    )


def _audit_tool_phases(a, cfg, flags, audit_dir, has_audit_md):
    """Script steps before the audits: extra tools, then (when AUDIT.md has an `audit-tools` block) the tool run and its context."""
    S = a.stage
    yield [
        step(
            "tools-check",
            "script",
            None,
            f"{tool_cmd('xtools.py')} check --install --proj {shlex.quote(audit_dir)} --needed-by {shlex.quote(os.path.join(audit_dir, cfg['audit_file']))} --stage-dir .work/{S}{' --browser' if flags['ui'] else ''} --report .work/{S}/audits/tools-check.md",
            {DONE},
            None,
            "before the audits: extra tools present",
        )
    ]
    if cfg.get(WIRING_DIRS_KEY):  # no wiring_dirs in pipeline.json: no orphan check
        yield [
            step(
                "orphan-check",
                "script",
                None,
                f"{tool_cmd('pipeline/plan_wiring_orphans.py')} --workspace {shlex.quote(os.path.abspath(a.dir))} --base {audit_base(a, cfg)} --out .work/{S}/audits/orphans.md",
                {DONE},
                None,
                "after the last wave: new source files that nothing imports, by script (a finding never blocks)",
            )
        ]
    if has_audit_md:  # no model: the audit tools run once, the auditors read the compact result
        yield [
            step(
                "audit-run",
                "script",
                None,
                f"{tool_cmd('audit/audit_run.py')} {shlex.quote(audit_dir)} --base {audit_base(a, cfg)} --verdict",
                {DONE},
                None,
                "before the audits: run the audit tools once, quietly",
            )
        ]
        yield [
            step(
                "audit-enrich",
                "script",
                None,
                f"{tool_cmd('audit/audit_enrich.py')} {shlex.quote(audit_dir)} --verdict",
                {DONE},
                None,
                "after audit-run: check unused-code findings with lp nav refs (best effort, tags only)",
            )
        ]
        yield [
            step(
                "audit-context",
                "script",
                None,
                f"{tool_cmd('audit/audit_context.py')} {shlex.quote(audit_dir)} --base {audit_base(a, cfg)} --head {_wb(a, cfg)} --out .work/{S}/audits/audit-context.md --workspace {shlex.quote(os.path.abspath(a.dir))}{block_args(a, cfg, audit_dir)} --verdict",
                {DONE},
                None,
                "before the audits: what changed and who uses it, by script (best effort)",
            )
        ]


def _ux_shots_step(a, audit_dir, sid, why):
    """build, serve, screenshots + layout numbers; no model"""
    return step(
        sid,
        "script",
        None,
        f"{tool_cmd('ux_run.py')} --stage {a.stage} --dir {shlex.quote(a.dir)} --repo {shlex.quote(audit_dir)} --out .work/{a.stage}/{sid}",
        {DONE},
        None,
        why,
    )  # build, serve, screenshots + layout numbers; no model


def reaudit_context_steps(a, cfg, audit_dir, has_audit_md, suffix) -> list[dict]:
    """-> script steps that rebuild audit-context.md (when AUDIT.md has audit tools) and plan-vs-changes.md after the fix commits, before the re-audit (step ids carry SUFFIX; same output files)."""
    S = a.stage
    base, head, out = audit_base(a, cfg), _wb(a, cfg), f".work/{S}/audits"
    ws = shlex.quote(os.path.abspath(a.dir))
    sts = []
    if has_audit_md:
        cmd = f"{tool_cmd('audit/audit_context.py')} {shlex.quote(audit_dir)} --base {base} --head {head} --out {out}/audit-context.md --workspace {ws}{block_args(a, cfg, audit_dir)} --verdict"
        sts.append(
            step("audit-context" + suffix, "script", None, cmd, {DONE}, None, "before the re-audit: context of the stage incl. the fix commits (best effort)")
        )
    cmd = f"{tool_cmd('localmodel/plan_context.py')} stage {shlex.quote(audit_dir)} --stage-dir .work/{S} --base {base} --head {head} --out {out}/plan-vs-changes.md --workspace {ws} --verdict"
    sts.append(
        step("plan-vs-changes" + suffix, "script", None, cmd, {DONE}, None, "before the re-audit: planned tasks vs changed files after the fixes (best effort)")
    )
    return sts


def _fix_phases(a, cfg, flags, tasks, audit_dir, has_audit_md, small, bad, rnd=1):
    """Fix round for the audits that said FIXES, then those audits once more (-2); an audit that was CLEAN is never re-run."""
    S = a.stage
    prev = "" if rnd == 1 else f"-{rnd}"  # suffix of the audit reports that asked for these fixes
    suffix = f"-{rnd + 1}"  # suffix of the re-audit that follows
    reports = ", ".join(f".work/{S}/audits/{k}{prev}.md" for k, _ in bad)
    # The fixes are planned like any other work: architect adds tasks and a later `wave K:` line to TASKS.md; decide() then runs them
    # through the normal waves (dev -> reviewer -> merger) before it gets here again, and only the audits that asked run a second time.
    yield [
        fix_step(
            a,
            tasks,
            f"fix-plan-{rnd}",
            [f".work/{S}/audits/{k}{prev}.md" for k, _ in bad],
            f"the audits found blocker / should-fix items in {reports}.",
            f"fix-plan-{rnd}",  # the lead cache is per round: round 2 must not get round 1's leads
            f"{', '.join(k + prev for k, _ in bad)} said FIXES",
        )
    ]
    # After the fix tasks have run: every audit that said FIXES runs once more (-2); an audit that was CLEAN is never re-run.
    if any(k == "audit-ux" for k, _ in bad):
        yield [_ux_shots_step(a, audit_dir, f"ux-shots{suffix}", "after the fixes: fresh screenshots for the ux re-check")]
    yield reaudit_context_steps(a, cfg, audit_dir, has_audit_md, suffix)
    yield [
        _audit_step(
            a,
            cfg,
            flags,
            has_audit_md,
            small,
            k,
            ag,
            {CLEAN, FIXES} if rnd == 1 else {CLEAN},  # after round 1 one more fix round is allowed; FIXES after the last one goes to the lead
            suffix,
            "asked for fixes: runs once more after they landed" + ("; fixes small: Haiku low" if small else "")
            if ag != "ux-reviewer"
            else "ux asked for fixes: cheap re-check",
        )
        for k, ag in bad
    ]


def _first_audits(a, cfg, audit_dir, has_ux):
    """The audits that run in parallel with arch on the first pass: sec unless `sec_audit_paths` rules it out, ux when it applies, the docs freshness audit once per feature (its finish phase)."""
    fresh = cfg.get("docs_fresh_audit") and plan_files.is_finish_dir(a.stage.split("/")[-1])
    return (
        ([("audit-sec", "sec-auditor")] if sec_audit_needed(a, cfg, audit_dir) else [])
        + ([("audit-ux", "ux-reviewer")] if has_ux else [])
        + ([("audit-fresh", FRESHNESS_AUDITOR)] if fresh else [])
    )


def _escalated_steps(a, cfg, flags, has_audit_md, small, evs, kinds):
    """-> the one Opus rerun step for each audit in KINDS whose first pass said ESCALATE (none yet run); an Opus rerun never escalates again."""
    return [
        _audit_step(a, cfg, flags, has_audit_md, small, k, ag, {CLEAN, FIXES}, "", "first pass said ESCALATE (areas left UNKNOWN): rerun once on Opus", True)
        for k, ag in kinds
        if ag in AUDITOR_ESCALATE and last_verdict(evs, k) == ESCALATE
    ]


def audit_verdict(evs, k) -> str | None:
    """-> the verdict that counts for audit K: the Opus rerun's when the first pass escalated, else the first pass's."""
    return last_verdict(evs, k + AUDIT_ESCALATE_SUFFIX) or last_verdict(evs, k)


def _scans_phase(a, cfg):
    if cfg.get("scans"):  # deterministic scans (gitleaks, cargo audit ...) by script: the auditors read the file
        yield [
            step(
                "scans",
                "script",
                None,
                f"{tool_cmd('audit/scans.py')} --dir {shlex.quote(a.dir)} --stage {a.stage}",
                {DONE},
                None,
                "before the audits: pipeline.json `scans` commands by script, output in scans.md",
            )
        ]


def _audit_passes(a, cfg, flags, has_audit_md, small, evs, first, beside=()):
    """The first audits and the arch audit in one parallel batch (BESIDE: extra steps that start with them), then the Opus rerun of each that said ESCALATE."""
    arch = ("audit-arch", "arch-auditor")
    batch = [
        _audit_step(a, cfg, flags, has_audit_md, small, k, ag, {CLEAN, FIXES, ESCALATE} if ag in AUDITOR_ESCALATE else {CLEAN, FIXES})
        for k, ag in [*first, arch]
    ]
    yield [*beside, *batch]
    esc = _escalated_steps(a, cfg, flags, has_audit_md, small, evs, [*first, arch])
    if esc:
        yield esc


def docs_overlap(writes, reads) -> bool:
    """True when a path the docs writer edits (a file, or a folder written `dir/`) is among the paths READS that an auditor reads."""
    return any(r == w or (w.endswith("/") and r.startswith(w)) for w in writes for r in reads)


def docs_beside_audits(a, cfg, audit_dir, writes=DOCS_WRITES) -> bool:
    """True when final-docs may start together with the audits: the stage diff (base..work branch) holds no file the docs writer edits. An unreadable or empty diff keeps them serial."""
    names = gitx.try_text(audit_dir, "-c", "core.quotepath=off", "diff", "-M", "--name-only", f"{audit_base(a, cfg)}...{_wb(a, cfg)}").splitlines()
    return bool(names) and not docs_overlap(writes, names)


def audit_rounds(a, cfg, flags, tasks, evs, beside=()) -> Iterator[list[dict]]:
    """Tool scripts, the audits (sec, ux and arch in one batch) and, when some said FIXES, the fix round and the re-audits."""
    audit_dir = audit_dir_of(a, cfg)
    audit_md = read_text(os.path.join(audit_dir, cfg["audit_file"]))
    has_ux = flags["ui"] and re.search(r"^```ux\s*$", audit_md, re.M)  # UX review is project specific: no `ux` block in AUDIT.md, no UX review
    first = _first_audits(a, cfg, audit_dir, has_ux)
    kinds = [*first, ("audit-arch", "arch-auditor")]

    top = [t for t in tasks if t["wave"] == max(t["wave"] for t in tasks)]  # the fix wave when this is a re-audit
    small = not any(t["owner_sonnet"] or t["review_sonnet"] for t in top) and flags["effort"] != "high"  # small, mechanical fixes: Haiku re-check

    has_audit_md = "```audit-tools" in audit_md

    yield from _audit_tool_phases(a, cfg, flags, audit_dir, has_audit_md)
    yield from _scans_phase(a, cfg)
    if has_ux:
        yield [_ux_shots_step(a, audit_dir, "ux-shots", "before ux-reviewer: screenshots and layout numbers by script")]
    yield from _audit_passes(a, cfg, flags, has_audit_md, small, evs, first, beside)
    bad = [(k, ag) for k, ag in kinds if audit_verdict(evs, k) == "FIXES"]
    if bad:
        yield from _fix_phases(a, cfg, flags, tasks, audit_dir, has_audit_md, small, bad)
        bad2 = [(k, ag) for k, ag in bad if last_verdict(evs, k + "-2") == "FIXES"]
        if bad2:  # second and last fix round; a FIXES from the -3 audits is not accepted: back to the lead
            yield from _fix_phases(a, cfg, flags, tasks, audit_dir, has_audit_md, small, bad2, 2)
