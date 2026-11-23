"""Verdict words, role names, the role table (allowed verdicts), auditors and merge marks."""

# verdict words (first line of a report: `VERDICT: X`)
PASS, FAIL, DONE, ESCALATE, BLOCKED = "PASS", "FAIL", "DONE", "ESCALATE", "BLOCKED"
NEED = "NEED"  # merger status line `NEED: <what>`: a denied commit or missing brief item; the verdict is BLOCKED, the stage is flagged
CLEAN, FIXES = "CLEAN", "FIXES"  # auditors
LANDED, MISSING = "LANDED", "MISSING"  # architect checking a fix
PLAN_ERROR = "PLAN-ERROR"  # reviewer report line `PLAN-ERROR: <what to fix in which task file>`: the task file (a Done-line symbol or path) is wrong, not the dev; the architect fixes it, no dev retry
SKIPPED = "SKIPPED"  # script step that did not do its work (local pre-review skipped): never a pass

# role names
DEV, REVIEWER, MERGER, TESTER, WEB_TESTER, DOCS_WRITER, OPS, SCRIPT, PUBLISHER, ARCHITECT = (
    "dev",
    "reviewer",
    "merger",
    "tester",
    "web-tester",
    "docs-writer",
    "ops",
    "script",
    "publisher",
    "architect",
)
MODEL_HAIKU, MODEL_SONNET, MODEL_OPUS = "haiku", "sonnet", "opus"  # model names of `claude --model` and the agent files
UX_REVIEWER = "ux-reviewer"
AUDITORS = ("sec-auditor", "arch-auditor", UX_REVIEWER)
AUDITOR_ESCALATE = ("sec-auditor", "arch-auditor")  # auditors that start on a cheaper model and may answer ESCALATE (unfinished areas) to get one Opus rerun
AUDITOR_START_MODEL = {
    "sec-auditor": MODEL_SONNET,
    "arch-auditor": MODEL_SONNET,
}  # first-pass model of an escalating auditor (owner 2026-11-08: sec was Opus); effort high still means Opus
AUDITOR_ESCALATE_MODEL = MODEL_OPUS  # model of the rerun after an auditor ESCALATE
AUDIT_ESCALATE_SUFFIX = "-opus"  # step id suffix of that rerun (audit-sec-opus); its report overwrites the first pass report

ROLES = {  # role -> (allowed verdicts, safe verdict when the report is unclear)
    DEV: ((DONE, ESCALATE, BLOCKED), BLOCKED),
    REVIEWER: ((PASS, FAIL, ESCALATE), FAIL),
    MERGER: ((DONE, FAIL, BLOCKED), FAIL),  # BLOCKED: a NEED (denied commit, missing brief) or a conflict-resolve that cannot resolve; the lead is asked
    TESTER: ((PASS, FAIL), FAIL),
    WEB_TESTER: ((PASS, FAIL, BLOCKED), FAIL),  # BLOCKED: the UI check could not run or was partial (verdicts.py)
    DOCS_WRITER: ((DONE, BLOCKED), BLOCKED),
    OPS: ((DONE, FAIL, BLOCKED), BLOCKED),  # the push / index / cleanup steps are told to answer DONE|FAIL
    SCRIPT: (
        (DONE, FAIL, BLOCKED, SKIPPED),
        FAIL,
    ),  # no model: the driver runs the command in the prompt; only the test run answers BLOCKED (cannot run: infrastructure)
    PUBLISHER: ((DONE, FAIL), FAIL),
    ARCHITECT: ((DONE, BLOCKED, LANDED, MISSING, ESCALATE), BLOCKED),  # DONE|BLOCKED planning a fix, LANDED|MISSING checking it
    **dict.fromkeys(AUDITORS, ((CLEAN, FIXES, BLOCKED), BLOCKED)),
    **dict.fromkeys(AUDITOR_ESCALATE, ((CLEAN, FIXES, BLOCKED, ESCALATE), BLOCKED)),  # first pass only: ESCALATE reruns the audit once on Opus
}

MERGE_CONFLICT_MARK = (
    "merge conflict"  # merge_wave.py report text for a git conflict; with pipeline.json `merge_fix_conflict_only` only a report holding it gets a merger
)
MERGE_FIX_TRIES = 1  # pipeline.json `merge_fix_tries`: automatic merger tries after a merge FAIL before the lead is asked (0 = ask at once)
MERGE_RED_MARK = (
    "tests are red after the merge"  # merge_wave.py report line for a red gate (not a conflict): graph.py dispatches a fix-gate dev, not the merger
)

FEATURE_LOCK = "feature.lock"  # inside the feature's WORK_DIR folder: one `wave.py feature` per feature: two of them would plan the same phase and write the same TASKS.md/Plan.md
RERUN_FROM = {  # role -> the history() rule of its automatic re-run (cli_wave.recover_unreported): a review re-run keeps the dev, a script re-run keeps the whole task
    REVIEWER: {"from": "review"},
    SCRIPT: {"from": "script"},
}
SEVERE_PATTERN = r"\b(FAIL|blocker|should-fix|should fix|critical|high|medium|must|wrong|risky)\b"  # severity words of the auditor ranks (agents/sec-auditor.md) plus the FAIL marks; audit_phases compiles it (re.I)

# verdicts that count as a finished step in `wave.py status`
STEP_OK = (PASS, DONE, CLEAN)

# reviewer FAIL with no finding line: unclear, the auto retry runs the reviewer once more (verdicts.verdict, cli_orch _auto_retry)
STUB_REASON = "reviewer FAIL without findings"
FINDING_SKIP_PATTERN = r"^\W*(?:VERDICT\s*:|REPORT\s+a\d+|wrong\W*\d+)"  # lines that are no finding: the VERDICT line, the counts line `wrong N, risky N, nit N`, the `REPORT aNN` line; verdicts.py compiles it (re.I)
