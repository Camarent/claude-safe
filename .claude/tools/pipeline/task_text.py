"""Field lines of a task file (`Goal:`, `Done:`, `Test:`, `Outputs:`...): a field counts only at the start of a line and outside fenced code blocks, so prose or a code sample that quotes `Done:` is never read as a field."""

import re

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _outside_fences(text) -> str:
    """-> TEXT without its fenced code blocks (``` or ~~~; a block closes on a fence of the same character, at least as long as its opening run)."""
    kept = []
    fence = None
    for line in text.splitlines():
        if fence:
            close = re.match(rf"^ {{0,3}}({re.escape(fence[0])}+)\s*$", line)
            if close and len(close[1]) >= len(fence):
                fence = None
            continue
        if m := _FENCE_RE.match(line):
            fence = m[1]
            continue
        kept.append(line)
    return "\n".join(kept)


def field(text, name) -> list[str]:
    """-> the values of every `NAME:` field line of TEXT, in file order (the value is stripped; an empty field gives "")."""
    pat = re.compile(rf"^{re.escape(name)}:[ \t]*(.*?)[ \t]*$", re.M)
    return pat.findall(_outside_fences(text))


def lines(text, *names) -> list[tuple[str, str]]:
    """-> (NAME, value) for every field line of TEXT whose name is one of NAMES, in file order."""
    if not names:
        return []
    alt = "|".join(re.escape(n) for n in names)
    pat = re.compile(rf"^({alt}):[ \t]*(.*?)[ \t]*$", re.M)
    return [(m[1], m[2]) for m in pat.finditer(_outside_fences(text))]
