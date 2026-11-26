"""One commit-subject validator, two policies: `commit.py` (strict) and `wt.py merge` (lenient)."""

import re

REVERT_RE = re.compile(r'(Revert|Reapply) "(.+)"')  # git's own subjects for `git revert`; the quoted text must be a valid subject itself


def policy(type_pat, area_pat, area_required, min_tail) -> re.Pattern[str]:
    """-> compiled `type(area): what` pattern. MIN_TAIL = least characters after the first non-space character of WHAT."""
    area = rf"\({area_pat}\)"
    area = area if area_required else f"(?:{area})?"
    return re.compile(rf"(?:{type_pat}){area}: \S.{{{min_tail},}}")


def subject_ok(s, subject_re) -> bool:
    """True for a one-line subject matching SUBJECT_RE, or `Revert "..."` / `Reapply "..."` around one (nested reverts allowed)."""
    if subject_re.fullmatch(s):
        return True
    m = REVERT_RE.fullmatch(s)
    return m is not None and subject_ok(m.group(2), subject_re)
