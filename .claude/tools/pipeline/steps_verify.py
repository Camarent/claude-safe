#!/usr/bin/env python3
"""Verify step after a BLOCKED dev that left a commit for a tests or environment reason: the task's Test lines, then its gates, run in the
job worktree (no model call). Prints `VERDICT: DONE` when both are green (the reviewer judges the commit), `VERDICT: FAIL` plus the output
(the dev reworks it), or `VERDICT: BLOCKED` when the checks cannot run (the lead decides).

    python3 -I /root/.claude/tools/pipeline/steps_verify.py --stage S --task task-N [--dir D]

Imported by steps.py for the dispatch line; run as a script by the driver."""

import argparse
import os
import re
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]  # -I drops the script dir; the imports below need the tools dir
from pipeline.configuration import BLOCKED, DONE, tool_cmd  # noqa: E402
from pipeline.util import read_text  # noqa: E402

VERIFY_WHY = "blocked dev checks: Test lines and gates in the job worktree; green goes to review, red goes back to the dev"
_OWNER_WORDS = re.compile(r"owner|decision|decide|scope|unsafe|security|permission|not allowed|forbidden|ambiguous|unclear|which of", re.I)
_CHECK_WORDS = re.compile(r"\btests?\b|environment|\benv\b|sandbox|network|install|timeout|timed out|toolchain|missing (?:tool|binary)|flaky", re.I)


def blocked_for_checks(rep: dict) -> bool:
    """True when the BLOCKED reason is about tests or the environment, not an owner decision, scope or safety question."""
    text = " ".join(str(rep.get(k) or "") for k in ("reason", "why")) + " " + read_text(rep.get("file") or "")
    return bool(_CHECK_WORDS.search(text)) and not _OWNER_WORDS.search(text)


def command(a, task_id: str) -> str:
    """The shell command of the verify step for task TASK_ID of stage A."""
    return f"{tool_cmd('pipeline/steps_verify.py')} --stage {a.stage} --task {task_id} --dir {getattr(a, 'dir', '.')}"


def run(stage: str, task: str, d: str) -> tuple[str, str]:
    """-> (verdict, text): the Test lines first, then the gates; a red step stops the run. No gates configured: the green Test lines decide."""
    import testrun  # lazy: testrun imports wt, which imports this package
    from audit import taskgates

    v, note = testrun.run(stage, task, d)
    if "skipped" in note:  # a Test line that did not run is no green
        return BLOCKED, note
    if v != DONE:
        return v, note
    gv, gnote = taskgates.run(stage, task, d)
    if gv == BLOCKED:  # taskgates has nothing to run (no `gates` in pipeline.json): the Test lines alone decide
        return DONE, f"Test lines green; {gnote}"
    return gv, gnote


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--dir", default=".")
    a = ap.parse_args(argv)
    v, note = run(a.stage, a.task, os.path.abspath(a.dir))
    print(f"VERDICT: {v}\n{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
