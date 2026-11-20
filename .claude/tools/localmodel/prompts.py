"""Prompt wording shared by the local-model tools. No imports, so core code (search/local.py) can use it with Strata down."""

JSON_ONLY = "Answer with ONE json object only: "
VERDICT_SHAPE = '{"verdict":"PASS|FAIL","reason":"<20 words>","evidence":"<exact line or empty>"}'
VERDICT_ANSWER = JSON_ONLY + VERDICT_SHAPE
REVIEW_FINDINGS_SHAPE = '{"verdict":"PASS|FAIL","reason":"<20 words>","findings":[{"where":"<file:line>","quote":"<the exact text of that line>","why":"<violated rule, 15 words>"}]}'
REVIEW_FINDINGS_ANSWER = "List EVERY violation you find, not only the first (up to 20 findings; none for PASS). " + JSON_ONLY + REVIEW_FINDINGS_SHAPE


def findings_lines(j, limit=20):
    """-> output lines `why  | where quote` for the findings of a review answer `j`; the old single `evidence` form counts as one finding."""
    found = [f for f in j.get("findings") or [] if isinstance(f, dict)][:limit]
    if not found and str(j.get("evidence", "")).strip():
        return [f"{j.get('reason', '')}  | {str(j['evidence']).strip()}"]
    out = []
    for f in found:
        quote = (str(f.get("quote", "")).strip().splitlines() or [""])[0]
        out.append(f"{str(f.get('why', '')).replace('|', '/')}  | {str(f.get('where', '')).strip()} {quote}".rstrip())
    return out
