#!/usr/bin/env python3
"""Block causes of a feature: count the BLOCKED and FAIL report events of orch.jsonl by fixed keyword rules on their reason text.
No model. The first rule whose keyword occurs as a whole word in the reason wins (order matters: cite before gate); a reason no rule matches counts as `other`, so the counts add up to the number of blocks."""

import re

CAUSE_RULES = (  # (cause, keywords in the lower-cased reason); the first rule that matches wins
    ("cite", ("cite", "citation")),
    ("gate", ("gate",)),
    ("ignored path", ("ignored",)),
    ("tooling", ("tooling", "command not found", "not found", "binary")),
)
# whole-word match: "gate" must not hit "investigate" or "aggregate"
CAUSE_PATTERNS = tuple((cause, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keys) + r")\b")) for cause, keys in CAUSE_RULES)
OTHER = "other"
BLOCK_VERDICTS = ("BLOCKED", "FAIL")  # report verdicts that count as a block


def cause_of(reason):
    """-> the cause name of one block reason, `other` when no rule matches."""
    low = (reason or "").lower()
    return next((cause for cause, pattern in CAUSE_PATTERNS if pattern.search(low)), OTHER)


def count(events):
    """-> {cause: n} for every BLOCKED or FAIL report event of orch.jsonl events; every cause is a key (0 when unseen)."""
    out = {cause: 0 for cause, _ in CAUSE_RULES}
    out[OTHER] = 0
    for e in events:
        if e.get("ev") == "report" and e.get("verdict") in BLOCK_VERDICTS:
            out[cause_of(e.get("reason"))] += 1
    return out


def line(counts):
    """-> the one digest line of the counts: the total of the blocks, then each cause."""
    total = sum(counts.values())
    return f"- block causes ({total} BLOCKED/FAIL reports): " + ", ".join(f"{cause} {n}" for cause, n in counts.items())
