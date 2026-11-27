#!/usr/bin/env python3
"""Report preflight by script: the dev report is checked against the task's `Done:` line before the local pre-review and the paid reviewer.
A Done line that names a tool (vulture, lizard, madge, knip) the report has no output of, asks a mutation the report does not name, asks a
candidate list the report lacks, or the report cites a bare file name, is a FAIL: the dev reworks it with the messages, no review round.
A changed test file (`--changed`, the diff's paths) without a named mutation is a message too.

    python3 -I /root/.claude/tools/pipeline/report_preflight.py --brief PATH --report PATH [--changed PATH ...]

Prints `VERDICT: DONE` (nothing to check, or every rule holds) or `VERDICT: FAIL` plus one message line per problem.
Imported by steps.py for the dispatch line."""

import argparse
import os
import re
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]  # -I drops the script dir; the imports below need the tools dir
from pipeline import cite_check  # noqa: E402 - sys.path is set on the line above first
from pipeline.configuration import DONE, FAIL, VERDICT_PREFIX  # noqa: E402 - sys.path is set above first

PREFLIGHT_WHY = "report preflight: the dev report checked against the task's Done line by script; FAIL goes back to the dev"
PREFLIGHT_TOOLS = ("vulture", "lizard", "madge", "knip")  # the audit tools of AUDIT.md: a Done line naming one needs its output in the report
DONE_LINE = re.compile(r"^Done:.*$", re.M)
MUTATION_NAMED = re.compile(r"mutation\s*[:\-]\s*\S", re.I)  # the report names the mutation it ran: `mutation: <what was changed>`
CANDIDATE_WORD = re.compile(r"\bcandidates?\b", re.I)
DIFF_PATH = re.compile(r"^diff --git a/\S+ b/(\S+)", re.M)  # one per changed file of a `git diff` text
TEST_FILE = re.compile(r"(^|/)test_[^/]*\.py$")  # a changed test: a test_*.py file of the diff


def _names(word: str, text: str) -> bool:
    return re.search(rf"\b{word}\b", text, re.I) is not None


def diff_paths(diff: str) -> list[str]:
    """-> the file paths a `git diff` text changes, in order."""
    return DIFF_PATH.findall(diff)


def _tool_msgs(done_text: str, report_text: str) -> list[str]:
    """-> one message per audit tool the Done line names and the report has no output of."""
    return [
        f"the Done line names {tool}; the report has no {tool} output" for tool in PREFLIGHT_TOOLS if _names(tool, done_text) and not _names(tool, report_text)
    ]


def _mutation_msgs(done_text: str, report_text: str, changed: list[str]) -> list[str]:
    """-> one message when the Done line asks a mutation or a test file changed, and the report names no mutation."""
    tests = [p for p in changed if TEST_FILE.search(p)]
    asked = _names("mutation", done_text)
    if not (asked or tests) or MUTATION_NAMED.search(report_text):
        return []
    why = "the Done line asks a mutation" if asked else "a changed test (" + ", ".join(tests) + ")"
    return [f"{why}; the report names none (`mutation: <what was changed>`)"]


def _bare_cite_msgs(report_text: str) -> list[str]:
    """-> one message when the report cites a file by its bare name (no directory)."""
    cited = [(ln, path, line) for ln, text in enumerate(report_text.splitlines(), 1) for path, line, *_ in cite_check.cites(text)]
    notes = [f"line {ln} cites {path}:{line}" for ln, path, line in cited]  # the cite_check message form: bare_names reads " cites <path>:"
    if not cite_check.bare_names(notes):
        return []
    bare = [n for n, (_, path, _) in zip(notes, cited, strict=True) if "/" not in path]
    return ["the report cites a bare file name; cite the full repo-relative path: " + "; ".join(bare)]


def check(done_text: str, report_text: str, changed: list[str] | tuple[str, ...] = ()) -> list[str]:
    """-> one message per rule the dev report breaks against the Done line text and the changed files; [] when every rule holds or the Done line asks nothing."""
    msgs = _tool_msgs(done_text, report_text) + _mutation_msgs(done_text, report_text, list(changed))
    if CANDIDATE_WORD.search(done_text) and not CANDIDATE_WORD.search(report_text):
        msgs.append("the Done line asks a candidate list; the report has none")
    return msgs + _bare_cite_msgs(report_text)


def done_line(brief_text: str) -> str:
    """-> the task brief's `Done:` line(s), or '' when the brief has none."""
    return "\n".join(DONE_LINE.findall(brief_text))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Report preflight by script: the dev report against the task's Done line.")
    p.add_argument("--brief", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--changed", action="append", default=[], help="a file the task's diff changes (repeat per file)")
    a = p.parse_args(argv)
    with open(a.brief, encoding="utf-8", errors="replace") as f:
        done = done_line(f.read())
    with open(a.report, encoding="utf-8", errors="replace") as f:
        report = f.read()
    msgs = check(done, report, a.changed) if done else []
    print(f"{VERDICT_PREFIX}{FAIL if msgs else DONE}")
    print("\n".join(msgs) or "report preflight: every Done rule holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
