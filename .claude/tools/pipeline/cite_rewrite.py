"""Rewrite bare module-name cites in a dev report to their full repo-relative path.

A cite `steps.py:274` names its file by the bare name; when exactly one file of the worktree has that name, the cite becomes
`.claude/tools/pipeline/steps.py:274`. An ambiguous or missing name stays unchanged, so the cite check still fails it."""

from . import cite_check


def rewrite_bare_cites(worktree: str, report_text: str) -> str:
    """-> report_text with each bare `name:line` cite replaced by its full path when exactly one worktree file has that name; else unchanged."""
    lines = []
    for line in report_text.splitlines(keepends=True):
        urls = [(m.start(), m.end()) for m in cite_check.CITE_URL_RE.finditer(line)]

        def fix(m, urls=urls):
            if any(s <= m.start() < e for s, e in urls):
                return m.group(0)  # a URL is not a cite
            path = cite_check._resolve_bare(worktree, m.group(1))
            return m.group(0) if path == m.group(1) else path + m.group(0)[len(m.group(1)) :]

        lines.append(cite_check.CITE_RE.sub(fix, line))
    return "".join(lines)
