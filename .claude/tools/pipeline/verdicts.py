"""Agent roles, their allowed verdicts and the verdict parser for a report."""

import re

from .configuration import (  # noqa: F401  # AUDITORS and ROLES are re-exported for the other modules
    AUDITORS,
    BLOCKED,
    CLEAN,
    DEV,
    DONE,
    ESCALATE,
    FIXES,
    MERGER,
    NEED,
    ROLES,
    UX_REVIEWER,
    WEB_TESTER,
)

NO_FINDINGS_RE = re.compile(r"^\W*wrong\W*0\W+risky\W*0\W+nit\W*0\W*$", re.I | re.M)  # the reviewer rubric counts line
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


def missing_note(role, reason):
    """The retry prompt for an unclear report: names the missing line and the words the role may answer with."""
    allowed, _ = ROLES[role]
    words = "|".join(allowed)
    if reason == "no report file written":
        return f"the previous run wrote no report file: write it, its first line `VERDICT: {words}`"
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


def verdict(role, text):
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
        if m.group(1) == ESCALATE and role == "reviewer" and NO_FINDINGS_RE.search(text) and not ESCALATE_LINE_RE.search(text):
            return "PASS", "ESCALATE with no findings (wrong 0, risky 0, nit 0)"  # nothing to fix: a second paid review would only repeat it
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
