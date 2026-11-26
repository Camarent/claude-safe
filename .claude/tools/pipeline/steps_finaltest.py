"""Final-test FAIL triage by script, no model: rerun the failing test files alone; green -> flaky (no fix plan);
red alone -> rerun them on the preflight head in a temp worktree; red there too -> PRE_EXISTING_RED (the lead decides); red only on the stage head -> the fix plan.

    python3 -I /root/.claude/tools/pipeline/steps_finaltest.py --dir WORKSPACE --stage S --report FILE [--base-head SHA]

Prints `VERDICT: DONE|FAIL|BLOCKED` and one reason line. `final_files_cmd` (pipeline.json) runs the files: `{files}` is replaced by them."""

import argparse
import os
import re
import shlex
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from audit import final_check

from pipeline import gitx
from pipeline.configuration import (
    DONE,
    FAIL,
    FINAL_FILES_CMD_KEY,
    FLAKY_LINE,
    PRE_EXISTING_RED,
    TEST_FILE_RE,
    project,
    repo_dir,
    test_env,
)

BLOCKED = "BLOCKED"  # the verdict word: red on the preflight head too (PRE_EXISTING_RED)


def test_files(text: str) -> list[str]:
    """-> the test file paths a failed final-test report names, in order, unique."""
    return list(dict.fromkeys(re.findall(TEST_FILE_RE, text)))


def rerun_cmd(cfg: dict, files: list[str]) -> str:
    """-> the `final_files_cmd` line with the FILES quoted in, '' when the project sets none or no file is named."""
    tmpl = cfg.get(FINAL_FILES_CMD_KEY) or ""
    if not tmpl or not files:
        return ""
    return tmpl.replace("{files}", " ".join(shlex.quote(f) for f in files))


def _red(cmd, repo, env, runner):
    """-> (red, output tail) of CMD run once in REPO."""
    res = final_check.run_all([cmd], repo, env, runner=runner)[0]
    return res[1] != 0, res[3]


class _BaseTree:
    """A temp worktree at HEAD of REPO, removed on exit (also when the run fails)."""

    def __init__(self, repo, head):
        self.repo, self.head, self.path = repo, head, ""

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="finaltest-base-")
        os.rmdir(self.path)  # git worktree add wants a missing folder
        gitx.out(self.repo, "worktree", "add", "--detach", self.path, self.head)
        return self.path

    def __exit__(self, *exc):
        try:
            gitx.out(self.repo, "worktree", "remove", "--force", self.path, check=False)
        finally:
            shutil.rmtree(self.path, ignore_errors=True)
            gitx.out(self.repo, "worktree", "prune", check=False)
        return False


def triage(cfg: dict, repo: str, env: dict, report_text: str, base_head: str = "", runner=final_check.run_group) -> tuple[str, str]:
    """-> (verdict, reason line). DONE flaky; FAIL the fix plan (red on the stage head only, or nothing to rerun); BLOCKED PRE_EXISTING_RED."""
    files = test_files(report_text)
    cmd = rerun_cmd(cfg, files)
    if not cmd:
        return FAIL, "no failing test file named or no `final_files_cmd` in pipeline.json: the fix plan"
    red, _ = _red(cmd, repo, env, runner)
    if not red:
        return DONE, f"{FLAKY_LINE} ({', '.join(files)})"
    if not base_head:
        return FAIL, f"red alone on the stage head ({', '.join(files)}); no preflight head to compare: the fix plan"
    with _BaseTree(repo, base_head) as base:
        base_red, tail = _red(cmd, base, env, runner)
    if base_red:
        last = tail.splitlines()[-1] if tail else ""
        return BLOCKED, f"{PRE_EXISTING_RED.replace('<command>', cmd)} (red on the preflight head {base_head[:12]} too; {', '.join(files)}) {last}".rstrip()
    return FAIL, f"red on the stage head only ({', '.join(files)}): the fix plan"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--stage", default="")
    ap.add_argument("--report", required=True)
    ap.add_argument("--base-head", default="")
    a = ap.parse_args(argv)
    cfg = project(a.dir)
    with open(a.report, encoding="utf-8") as f:
        text = f.read()
    verdict, reason = triage(cfg, repo_dir(a.dir, cfg), {**os.environ, **test_env(cfg, a.dir)}, text, a.base_head)
    print(f"VERDICT: {verdict}\n{reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
