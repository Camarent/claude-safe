#!/usr/bin/env python3
"""research_apply: write a scout report body to the one `Outputs:` path of a task, in its job worktree, and commit it.

    python3 -I /root/.claude/tools/research_apply.py --stage S --task task-N --dir D --report R

The report file R is the scout's answer: its first `VERDICT: X` line is dropped, the rest is the body.
Prints `DONE: ...` (exit 0) when the body is committed, or when the same body is already committed (re-run: nothing to do),
and `FAIL: <why>` (exit 1) when the body is empty, the task has no or several `Outputs:` paths, the path leaves the worktree,
or any file other than the output path would change or be committed."""

import argparse
import os
import sys
from types import SimpleNamespace

sys.path[:0] = [os.path.dirname(os.path.abspath(__file__))]  # -I drops the script dir
from pipeline import gitx, worktree_merge, worktrees  # noqa: E402  (sys.path set above)
from pipeline.configuration import WORK_DIR  # noqa: E402  (sys.path set above)
from pipeline.plan_files import task_outputs  # noqa: E402  (sys.path set above)
from pipeline.util import read_text, work_path  # noqa: E402  (sys.path set above)

SUBJECT = "docs(research): scout report"  # commit subject of the applied report
VERDICT_PREFIX = "VERDICT:"


def body_of(text: str) -> str:
    """The report text without its first `VERDICT:` line, stripped of surrounding blank lines."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(VERDICT_PREFIX):
            del lines[i]
            break
    return "\n".join(lines).strip("\n")


def changed_paths(root: str) -> set[str]:
    """Paths with a change (modified, added, deleted or untracked) in the worktree root."""
    out = gitx.run(root, "status", "--porcelain", "--untracked-files=all").stdout
    return {line[3:].strip().strip('"') for line in out.splitlines() if line.strip()}


def committed_paths(root: str) -> set[str]:
    """Paths the HEAD commit of the worktree root changed."""
    out = gitx.run(root, "show", "--name-only", "--format=", "HEAD").stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def apply(d: str, stage: str, task: str, report: str) -> tuple[int, str]:
    """Write and commit the report body. -> (exit code, one-line result)."""
    if not os.path.isfile(report):
        return 1, f"FAIL: no report file {report}"
    try:
        body = body_of(read_text(report))
        brief = read_text(work_path(os.path.join(d, WORK_DIR, stage), task + ".md"))
    except (OSError, UnicodeDecodeError) as ex:
        return 1, f"FAIL: cannot read report {report} or the brief of {task}: {ex}"
    if not body:
        return 1, f"FAIL: empty report body in {report}"
    outs = task_outputs(brief)
    if len(outs) != 1:
        return 1, f"FAIL: task {task} needs exactly one Outputs: path, has {len(outs)}"
    rel = outs[0]
    if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        return 1, f"FAIL: Outputs: path {rel!r} leaves the worktree"
    info = worktrees.job_info(SimpleNamespace(stage=stage, dir=d, feature=None), task, create=True)
    if not info:
        return 1, f"FAIL: no job worktree for {task} (worktrees are off)"
    root = info["root"]
    other = changed_paths(root) - {rel}
    if other:
        return 1, f"FAIL: worktree has changes outside {rel}: {sorted(other)}"
    target = os.path.join(root, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    changed = changed_paths(root)
    if not changed:
        return 0, f"DONE: {rel} already committed, nothing to do"
    if changed != {rel}:
        return 1, f"FAIL: applying the report would change {sorted(changed - {rel})}, not only {rel}"
    # regen=False: no docs index rewrite, so the commit holds exactly the staged output path; the check after it is a guard
    shas = worktree_merge.commit(d, stage, task, SUBJECT, regen=False)
    if committed_paths(root) != {rel}:
        return 1, f"FAIL: commit changed {sorted(committed_paths(root))}, not only {rel}"
    return 0, f"DONE: {rel} committed {shas.get('.')}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Commit a scout report body to the task's Outputs: path in its job worktree.")
    ap.add_argument("--stage", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--dir", default=".")
    ap.add_argument("--report", required=True)
    a = ap.parse_args(argv)
    code, line = apply(os.path.abspath(a.dir), a.stage, a.task, a.report)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
