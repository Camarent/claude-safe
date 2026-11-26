"""prepare-repo: add the pipeline's local files to `.git/info/exclude` and the tracked `.gitignore` of the repo (`nested_repo_excludes` to every nested repo), the `.gitignore` lines of the detected languages, and a git-excluded CLAUDE.md with a `## Docs` section (repo has a docs folder) and a `## Guidelines` section (detected languages). Only appends: an existing line is never changed or moved. Idempotent.
It also REPORTS (writes nothing): malformed AUDIT.md audit-tools lines, a missing `worktrees` setting (`--add-worktrees` adds `"worktrees": true` to pipeline.json), gate tools missing from PATH.
`--dry-run` prints what would be added and changes nothing.

python3 -I /root/.claude/tools/pipeline/prepare_repo.py [--dir D] [--dry-run] [--add-worktrees]
"""

import argparse
import os
import re
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]  # -I drops the script dir
from pipeline import gitx  # noqa: E402
from pipeline.configuration import (  # noqa: E402
    CLAUDE_FILE,
    DOCS_HEADING,
    DOCS_SECTION_TEMPLATE,
    EXCLUDE_FILE,
    GITIGNORE_FILE,
    GUIDELINES_BASE_LINE,
    GUIDELINES_DIR,
    GUIDELINES_HEADING,
    GUIDELINES_LANG_LINE,
    GUIDELINES_RUNBOOK_LINES,
    GUIDELINES_SECTION_TEMPLATE,
    LANG_IGNORES,
    PIPELINE_EXCLUDES,
    PIPELINE_JSON,
    PRACTICES_DIR,
    WORKTREES_KEY,
    project,
)
from pipeline.guidelines import detect  # noqa: E402
from pipeline.prepare_checks import malformed_audit_lines, toolchain_problems, worktrees_missing  # noqa: E402
from pipeline.util import read_text  # noqa: E402
from pipeline.worktree import nested_repos  # noqa: E402


def _raw(path):
    """-> the file's text exactly as stored (no newline translation), "" when missing."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _eol(text):
    """-> the line ending the file already uses (CRLF when it has any, else LF)."""
    return "\r\n" if "\r\n" in text else "\n"


def _norm(line):
    """A .gitignore line without leading and trailing slashes: `node_modules` and `/node_modules/` name the same directory."""
    return line.strip().strip("/")


def _missing(entries, text):
    """-> the ENTRIES the .gitignore-style TEXT lacks: an entry counts as present when a line names the same path (slashes ignored) and as refused when a `!` line negates it (`!/build/` negates `build/`)."""
    lines = [ln.strip() for ln in text.splitlines()]
    seen = {_norm(ln) for ln in lines if not ln.startswith("!")} | {_norm(ln[1:]) for ln in lines if ln.startswith("!")}
    return [e for e in entries if _norm(e) not in seen]


def _append(path, entries, blank_line=False, dry=False):
    """Append the entries that the file lacks; -> the entries added (`dry`: only listed, nothing written). `blank_line`: a blank line separates the block from old text. The file's own line ending is kept, an entry that differs only by slashes counts as present and an entry the file negates (`!entry`) is not added."""
    text = _raw(path)
    eol = _eol(text)
    new = _missing(entries, text)
    if new and not dry:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not text:
            lead = ""
        elif blank_line:
            lead = "" if text.endswith(eol * 2) else (eol if text.endswith(eol) else eol * 2)
        else:
            lead = "" if text.endswith("\n") else eol
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(lead + eol.join(new) + eol)
    return new


def _entries_in(repo, entries, rel, dry=False):
    """Add entries to the exclude file and the tracked .gitignore of `repo`; -> 'file: entry' strings. `rel` is the repo's path under the root."""
    exclude = gitx.out(repo, "rev-parse", "--git-path", EXCLUDE_FILE)
    exclude = exclude if os.path.isabs(exclude) else os.path.join(repo, exclude)
    added = [os.path.join(rel, ".git", EXCLUDE_FILE) + ": " + e for e in _append(exclude, entries, dry=dry)]
    added += [os.path.join(rel, GITIGNORE_FILE) + ": " + e for e in _append(os.path.join(repo, GITIGNORE_FILE), entries, blank_line=True, dry=dry)]
    return added


def _lang_ignores(repo):
    """-> the .gitignore lines of every language whose marker file is in the repo root."""
    found = [e for marks, entries in LANG_IGNORES if any(os.path.exists(os.path.join(repo, m)) for m in marks) for e in entries]
    return [e for e in found if not (e.endswith("/") and gitx.try_out(repo, "ls-files", "--", e))]  # a folder the repo tracks is not ignored


def _is_tracked(repo, name):
    return gitx.run(repo, "ls-files", "--error-unmatch", name).returncode == 0


def _docs_text(repo):
    """-> the `## Docs` section text, "" when the repo has no docs folder."""
    docs = str(project(repo).get("docs_dir") or "")
    if not docs or not os.path.isdir(os.path.join(repo, docs)):
        return ""
    return DOCS_SECTION_TEMPLATE.format(heading=DOCS_HEADING, dir=docs.strip("/"))


def _guidelines_text(repo):
    """-> the `## Guidelines` section text for the detected languages, "" when none is detected."""
    langs = detect(repo)
    if not langs:
        return ""
    lines = [GUIDELINES_LANG_LINE.format(lang=lang, dir=GUIDELINES_DIR) for lang in langs] + [GUIDELINES_BASE_LINE.format(dir=GUIDELINES_DIR)]
    lines += [ln.format(dir=PRACTICES_DIR) for ln in GUIDELINES_RUNBOOK_LINES]
    return GUIDELINES_SECTION_TEMPLATE.format(heading=GUIDELINES_HEADING, lines="\n".join(lines))


def _claude_sections(repo, dry):
    """-> 'file: line' strings for a new CLAUDE.md (`## Docs`, `## Guidelines`) and its exclude line; [] unless there is a section to write, no CLAUDE.md, and it is not tracked."""
    path = os.path.join(repo, CLAUDE_FILE)
    sections = [s for s in (_docs_text(repo), _guidelines_text(repo)) if s]
    if not sections or os.path.lexists(path) or _is_tracked(repo, CLAUDE_FILE):
        return []  # an existing (or tracked) CLAUDE.md is the owner's: `report` names the missing sections
    text = "\n".join(sections)
    added = [f"{CLAUDE_FILE}: {ln}" for ln in text.splitlines() if ln]
    if not dry:
        with open(path, "x", encoding="utf-8") as fh:  # never clobbers a file that appeared meanwhile
            fh.write(text)
    exclude = gitx.out(repo, "rev-parse", "--git-path", EXCLUDE_FILE)
    exclude = exclude if os.path.isabs(exclude) else os.path.join(repo, exclude)
    return added + [os.path.join(".git", EXCLUDE_FILE) + ": " + e for e in _append(exclude, [CLAUDE_FILE], dry=dry)]


def _add_worktrees(repo, dry):
    """-> ['pipeline.json: "worktrees": true'] after adding the key textually (existing text untouched); [] when it is set, the file is not a JSON object, or nothing to do."""
    if not worktrees_missing(repo):
        return []
    path = os.path.join(repo, PIPELINE_JSON)
    text = _raw(path)
    eol = _eol(text)
    line = f'"{WORKTREES_KEY}": true'
    if text.strip():
        m = re.match(r"\s*\{", text)
        if not m:
            return []
        rest = text[m.end() :]
        text = text[: m.end()] + (f"{eol}  {line}{eol}" + rest.lstrip() if rest.lstrip().startswith("}") else f"{eol}  {line}," + rest)
    else:
        text = "{" + eol + "  " + line + eol + "}" + eol
    if not dry:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return [f"{PIPELINE_JSON}: {line}"]


def prepare(repo: str, dry_run: bool = False, add_worktrees: bool = False) -> list[str]:
    """-> what was added, or with `dry_run` what would be (empty when everything was in place). RuntimeError when `repo` is no git repo."""
    added = _entries_in(repo, PIPELINE_EXCLUDES, "", dry_run)
    langs = _lang_ignores(repo)
    added += [GITIGNORE_FILE + ": " + e for e in _append(os.path.join(repo, GITIGNORE_FILE), langs, blank_line=True, dry=dry_run)]
    added += _claude_sections(repo, dry_run)
    if add_worktrees:
        added += _add_worktrees(repo, dry_run)
    excludes = tuple(project(repo).get("nested_repo_excludes") or ())  # pipeline.json: [] = no entries in nested repos
    if excludes:
        for rel in nested_repos(repo):
            added += _entries_in(os.path.join(repo, rel), excludes, rel, dry_run)
    return added


def report(repo: str) -> list[str]:
    """-> problem lines nothing is written for: malformed audit-tools lines, no `worktrees` setting, gate tools missing."""
    out = malformed_audit_lines(repo)
    if worktrees_missing(repo):
        out.append(
            f'{PIPELINE_JSON}: no `{WORKTREES_KEY}` setting: parallel tasks in one checkout see each other\'s half-done files; rerun with --add-worktrees to add `"{WORKTREES_KEY}": true`'
        )
    return out + _docs_section_missing(repo) + _guidelines_section_missing(repo) + toolchain_problems(repo)


def _docs_section_missing(repo):
    """-> a report line when the repo has a docs folder and an existing CLAUDE.md without a `## Docs` section (it is never edited here: final-docs needs the section or a `docs:` line)."""
    docs = str(project(repo).get("docs_dir") or "")
    text = read_text(os.path.join(repo, CLAUDE_FILE))
    if not docs or not os.path.isdir(os.path.join(repo, docs)) or not text or re.search(r"^" + re.escape(DOCS_HEADING) + r"\b", text, re.M):
        return []
    return [f"{CLAUDE_FILE}: no `{DOCS_HEADING}` section: add one naming `{docs.strip('/')}/` so final-docs has a target (this file is not edited here)"]


def _guidelines_section_missing(repo):
    """-> a report line when languages are detected and an existing CLAUDE.md has no `## Guidelines` section (it is never edited here)."""
    langs = detect(repo)
    text = read_text(os.path.join(repo, CLAUDE_FILE))
    if not langs or not text or re.search(r"^" + re.escape(GUIDELINES_HEADING) + r"\b", text, re.M):
        return []
    return [f"{CLAUDE_FILE}: no `{GUIDELINES_HEADING}` section: add one pointing at the guidelines for {', '.join(langs)} (this file is not edited here)"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--dry-run", action="store_true", help="print what would be added; change nothing")
    ap.add_argument("--add-worktrees", action="store_true", help=f'add `"{WORKTREES_KEY}": true` to {PIPELINE_JSON} when it has no such key')
    a = ap.parse_args(argv)
    planned = prepare(a.dir, True, a.add_worktrees)  # the list is printed before anything is written
    if planned:
        print(("would add:\n" if a.dry_run else "will add:\n") + "\n".join("  " + x for x in planned))
    if not a.dry_run:
        prepare(a.dir, False, a.add_worktrees)
        print("added: " + ", ".join(planned) if planned else "already prepared")
    elif not planned:
        print("already prepared")
    for line in report(a.dir):
        print("report: " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
