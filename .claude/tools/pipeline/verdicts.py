"""Agent roles, their allowed verdicts and the verdict parser for a report."""

import re

from .configuration import (  # noqa: F401  # AUDITORS and ROLES are re-exported for the other modules
    AUDITORS,
    BLOCKED,
    CLEAN,
    DEV,
    DONE,
    ESCALATE,
    FAIL,
    FIXES,
    MERGER,
    NEED,
    REVIEWER,
    ROLES,
    UX_REVIEWER,
    WEB_TESTER,
)
from .configuration.verdicts import FINDING_SKIP_PATTERN, STUB_REASON

ZERO_COUNTS_RE = re.compile(
    r"^\W*wrong\W*0\W+risky\W*0\W+nit\W*0\b", re.I | re.M
)  # the reviewer rubric counts line, `wrong 0, risky 0, nit 0`, on its own line
ESCALATE_DEFECT_RE = re.compile(r"\b[\w./-]+\.\w+:\d+\b")  # E1(c): a reviewer ESCALATE names one defect as file:line, e.g. `<file>:<line>`
WRONG_NONZERO_RE = re.compile(
    r"^\W*wrong\W*[1-9]|^\W*wrong\W*0\W+risky\W*[1-9]", re.I | re.M
)  # a wrong or risky finding anywhere in the report keeps the ESCALATE
ZERO_TAIL_LINES = 8  # the wrong-0 counts line sits among the last lines of a reviewer report
ESCALATE_LINE_RE = re.compile(r"^\s*ESCALATE:\s*\S", re.M)  # reviewer.md: the line that names a security class
NEED_LINE_RE = re.compile(
    rf"^\s*\W*{re.escape(NEED)}\W*:\s*(\S.*)$", re.M
)  # merger.md: `NEED: <what>` = the merge cannot finish (denied commit, missing brief)
FINAL_WORD_RE = re.compile(r"^\W*([A-Z]+)\W*$")  # a bare status word alone on the last non-empty line, e.g. `DONE` or `**FAIL**`


UNVERIFIED_RE = re.compile(
    r"^\s*(?:UNVERIFIED|PARTIAL)\b|^\s*VERDICT\s*:\s*PASS\W+(?:partial|unverified)\b", re.I | re.M
)  # web-tester.md: a UI check that could not run or skipped items


def _final_word(text):
    """The bare word on the last non-empty line of a report, or None."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    m = FINAL_WORD_RE.match(lines[-1]) if lines else None
    return m.group(1) if m else None


UNCLEAR_REASONS = ("no clear verdict", "no report file written", STUB_REASON)  # the dev reasons the auto retry (cli_orch _auto_retry) has already run for
KILLED_RC = "rc=-"  # a claude run ended by a signal (`wave: claude rc=-9`): as unclear as a missing verdict, retried the same way
FINDING_SKIP_RE = re.compile(FINDING_SKIP_PATTERN, re.I)


def is_unclear(reason):
    """True when the report reason is one the auto retry runs for: no verdict line, no report file, a FAIL without findings, or a run killed by a signal."""
    return reason in UNCLEAR_REASONS or KILLED_RC in (reason or "")


def missing_note(role, reason):
    """The retry prompt for an unclear report: names the missing line and the words the role may answer with."""
    allowed, _ = ROLES[role]
    words = "|".join(allowed)
    if reason == "no report file written":
        return f"the previous run wrote no report file: write it, its first line `VERDICT: {words}`"
    if reason == STUB_REASON:
        return "the previous FAIL report had no finding: list each finding as file:line, scenario, severity"
    return f"the previous report had no `VERDICT:` line: put `VERDICT: {words}` on its own line in the report (the first line, as the brief says); no other word counts"


def _dev_heuristic_done(role, text):
    return (
        role == DEV
        and not re.search(rf"\b({BLOCKED}|cannot|unable to)\b", text, re.I)
        and (re.search(r"\b(pass|passed|passes|green)\b", text, re.I) or re.search(r"\bOK\b", text))
    )


def _partial_ui_pass(role, m, text):
    return bool(m) and m.group(1) == "PASS" and role == WEB_TESTER and bool(UNVERIFIED_RE.search(text))


UX_UNVERIFIED_RE = re.compile(
    r"^\s*UNVERIFIED\s*:\s*(?!none\b)\S", re.I | re.M
)  # a screen named after the marker; `UNVERIFIED: none` and prose starting with "Partial" do not count


def _unverified_clean(role, m, text):
    """True for a UX audit that says CLEAN yet lists UNVERIFIED screens: a changed screen nobody saw is no clean audit."""
    return bool(m) and m.group(1) == CLEAN and role == UX_REVIEWER and bool(UX_UNVERIFIED_RE.search(text))


def _zero_wrong_downgrade(role, m, text):
    """True for a reviewer ESCALATE with `wrong 0, risky 0, nit 0` among the last lines, no finding `wrong N` (N>0) and no `ESCALATE:` line: nothing to fix, PASS with notes."""
    if not (bool(m) and m.group(1) == ESCALATE and role == "reviewer"):
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = "\n".join(lines[-ZERO_TAIL_LINES:])
    return bool(ZERO_COUNTS_RE.search(tail)) and not WRONG_NONZERO_RE.search(text) and not ESCALATE_LINE_RE.search(text)


def _reviewer_undefined_escalate(role, m, text):
    """True for a reviewer ESCALATE with no `ESCALATE:` security line and no defect named as file:line: nothing for the dev to act on, so the reviewer runs once more."""
    return bool(m) and m.group(1) == ESCALATE and role == "reviewer" and not ESCALATE_LINE_RE.search(text) and not ESCALATE_DEFECT_RE.search(text)


def _has_finding(text):
    """True when a report holds a non-blank line that is no VERDICT, counts or REPORT line."""
    return any(ln.strip() and not FINDING_SKIP_RE.match(ln.strip()) for ln in text.splitlines())


def _reviewer_stub(role, m, text):
    """True for a reviewer FAIL with no finding line: nothing for the dev to act on, so the reviewer runs once more."""
    return bool(m) and m.group(1) == FAIL and role == REVIEWER and not _has_finding(text)


def verdict(role, text, report_id=""):
    """-> (verdict, reason). `VERDICT: X` on its own line wins; else ESCALATE:/dev heuristics; unclear = the role's safe verdict."""
    allowed, safe = ROLES[role]
    if role == MERGER:
        need = NEED_LINE_RE.search(text)
        if need:  # a NEED line flags the stage even when the report also says FAIL or DONE: never a silent pass or a merge-fix
            return BLOCKED, f"merger {NEED}: {need.group(1).strip()[:200]}"
    m = re.search(r"^\s*VERDICT\s*:\s*([A-Z]+)", text, re.M)
    if _unverified_clean(role, m, text):
        return FIXES, "UX audit CLEAN with UNVERIFIED screens: the missing flow or check is a fix item"
    if _partial_ui_pass(role, m, text):
        return BLOCKED, "UI check partial or not run: a PASS needs every checklist item verified"
    if m:  # an explicit line decides, even a wrong one (then the safe verdict)
        if _zero_wrong_downgrade(role, m, text):
            return (
                "PASS",
                f"escalate_downgraded: ESCALATE with wrong 0, risky 0, nit 0 and no security line: PASS with notes, report {report_id or '-'}",
            )  # nothing to fix: a second paid review would only repeat it
        if _reviewer_stub(role, m, text) or _reviewer_undefined_escalate(role, m, text):
            return FAIL, STUB_REASON  # no finding line (FAIL) or no defect at file:line (ESCALATE): unclear, the auto retry runs the reviewer once more
        if m.group(1) in allowed:
            return m.group(1), "report line"
        return safe, f"verdict {m.group(1)} not valid for {role}"
    if ESCALATE in allowed and re.search(rf"^\s*{ESCALATE}\s*:", text, re.M):
        return ESCALATE, "heuristic"
    final = _final_word(text)
    if final in allowed:  # no VERDICT line, but the report ends with a bare status word the role may use
        return final, "final status line"
    if _dev_heuristic_done(role, text):
        return DONE, "heuristic"
    return safe, "no clear verdict"
