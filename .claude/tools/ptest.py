"""ptest: run the pipeline unit tests one file per process, in parallel.

Usage: python3 -I ptest.py [--jobs N] [--dir TESTS_DIR] [--cov] [PATTERN ...]
--cov measures coverage per test file (data files in a scratch dir), combines them and prints the report with the pyproject floor (fail_under).
PATTERN is a test file glob (default test_*.py). Prints the slowest files, then the failing ones with their last line.
Exit 0 when every file passes, 1 otherwise."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from pipeline import gitx
from pipeline.configuration import DEFAULT_WORKSPACE, JOB_BRANCH_PREFIX, MERGE_ONLY_TESTS_KEY, project

ENV_FAIL_ON_SKIP = "CI"  # set to 1: a skipped test fails the run (skips hide gaps; the container image has every tool)
TAIL_LINES = 30  # output lines shown under a FAIL line
ENV_LOG_DIR = "PTEST_LOG_DIR"  # env: folder for the last failing output of each failing file (point it at the session scratchpad)
LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".work", "ptest"
)  # default when the env is unset: inside the repo's ignored .work/
HERE = os.path.dirname(os.path.abspath(__file__))
# Test repos never need background housekeeping: each commit would otherwise start `git maintenance` (404 runs per suite).
QUIET_GIT = {
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "gc.auto",
    "GIT_CONFIG_VALUE_0": "0",
    "GIT_CONFIG_KEY_1": "maintenance.auto",
    "GIT_CONFIG_VALUE_1": "false",
}


def cov_rc(cov_dir):
    """Write a coverage rc file with absolute paths (tests run from their own dir); -> its path. Same settings as pyproject.toml."""
    rc = os.path.join(cov_dir, "rc")
    with open(rc, "w") as f:
        f.write(f"[run]\nsource = {HERE}\nomit = */tests/*\nbranch = True\nparallel = True\npatch = subprocess\ndata_file = {cov_dir}/.coverage\n")
    return rc


def report_cov(cov_dir):
    """Combine the per-file data and print the report with the pyproject floor. -> exit code; removes COV_DIR."""
    root = os.path.dirname(os.path.dirname(HERE))
    try:
        base = [sys.executable, "-I", "-m", "coverage"]
        data = f"--data-file={cov_dir}/.coverage"
        subprocess.run([*base, "combine", "-q", data, cov_dir], cwd=root, check=False)
        r = subprocess.run([*base, "report", data, f"--rcfile={root}/pyproject.toml", "--skip-covered"], cwd=root, check=False)
        return r.returncode
    finally:
        shutil.rmtree(cov_dir, ignore_errors=True)


def output_tail(p):
    """The last TAIL_LINES lines of a finished process's stdout and stderr, for the failure report."""
    lines = (p.stdout + p.stderr).strip().splitlines()
    return "\n".join(lines[-TAIL_LINES:])


def run_file(tests_dir, name, cov_dir=None):
    """(seconds, returncode, name, last stderr line, last TAIL_LINES lines of its output, whole output) of one test file run alone."""
    start = time.time()
    # Each file gets its own TMPDIR, removed afterwards: tests (and the tools they start) leak temp dirs otherwise, and a few suite runs fill a tmpfs /tmp (inodes).
    scratch = tempfile.mkdtemp(prefix="ptest-")
    try:
        cmd = [sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", name]
        if cov_dir:
            cmd[2:2] = ["-m", "coverage", "run", f"--rcfile={cov_dir}/rc"]
        p = subprocess.run(
            cmd,
            cwd=tests_dir,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            # ORCH_NO_INDEX: no detached background indexer may outlive the file and rewrite its removed temp dir
            env={**QUIET_GIT, "ORCH_NO_INDEX": "1", **os.environ, "TMPDIR": scratch},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    tail = (p.stderr.strip().splitlines() or [""])[-1]
    return time.time() - start, p.returncode, name, tail, output_tail(p), p.stdout + p.stderr


def keep_failure_log(name, text):
    """Write the whole output of a failing file to <log dir>/<name>.log, replacing the previous one; -> its path."""
    log_dir = os.environ.get(ENV_LOG_DIR) or LOG_DIR
    path = os.path.join(log_dir, name + ".log")
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(text)
    except OSError:  # read-only checkout (the release container mounts it :ro): the log is a bonus, never fail the run for it
        return "(not saved: log dir not writable)"
    return path


def merge_only_tests():
    """-> test file names only the merge gate runs (configuration merge_only_tests). A PATTERN that names one selects it."""
    return project(DEFAULT_WORKSPACE)[MERGE_ONLY_TESTS_KEY]


def select_files(tests_dir, patterns, merge_only=()):
    """Test file names matching the patterns, largest first: they tend to be the slowest, so the longest job starts early instead of last.
    A merge-only file is left out unless a pattern names it exactly."""
    # A pattern given as a repo path (.claude/tools/tests/test_x.py) selects by its file name: the tests dir is flat.
    named = {Path(pat).name for pat in patterns}
    names = sorted({f.name for pat in patterns for f in Path(tests_dir).glob(Path(pat).name) if f.name not in merge_only or f.name in named})
    names.sort(key=lambda n: -os.path.getsize(os.path.join(tests_dir, n)))
    return names


def unmatched_patterns(tests_dir, patterns):
    """Patterns that match no file in tests_dir. A named test file that is missing must fail the run, not pass as one of several selected files."""
    return [pat for pat in patterns if not any(Path(tests_dir).glob(Path(pat).name))]


def print_slowest(res):
    """Print the five slowest files."""
    for t, _, n, _, _, _ in res[:5]:
        print(f"{t:6.1f}s {n}")


def print_failures(bad):
    """Print each failing file with its last line and output tail, and where its full output was kept."""
    for _, _, n, tail, out, text in bad:
        print(f"FAIL {n}: {tail}")
        print(out)
        print(f"ptest: full output of {n} in {keep_failure_log(n, text)}")


def count_skipped(res):
    """Total of the skipped=N counts in the runs' last stderr lines."""
    return sum(int(m.group(1)) for r in res if (m := re.search(r"skipped=(\d+)", r[3])))


def main_checkout():
    """Directory of the main checkout (the one owning the shared .git, also when run from a worktree); "" when git is unavailable."""
    common = gitx.try_text(HERE, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return os.path.dirname(common) if common else ""


def main_claude_status():
    """`git status --porcelain` lines under .claude/ in the main checkout (the one owning the shared .git, also when run from a worktree); empty set when git is unavailable."""
    main_dir = main_checkout()
    if not main_dir:  # no git (release container): nothing to compare, never fail the run
        return set()
    return set(gitx.try_text(main_dir, "status", "--porcelain", "--", ".claude").splitlines())


def warn_new_claude_changes(before, after):
    """Warn (never fail) about main-checkout .claude/ changes that appeared during the run: a dev agent wrote outside its worktree. -> the new lines."""
    new = sorted(after - before)
    if new:
        print("ptest: WARNING: uncommitted .claude/ changes appeared in the main checkout during this run (a dev wrote outside its worktree?):")
        for line in new[:20]:
            print(f"  {line}")
    return new


def main_branch_state():
    """(current branch, sorted local branch names) of the main checkout (the one owning the shared .git, also when run from a worktree); job branches (JOB_BRANCH_PREFIX) are left out: a job worktree creates them mid-run by design. ("", []) when git is unavailable."""
    main_dir = main_checkout()
    if not main_dir:  # no git (release container): nothing to compare, never fail the run
        return "", []
    branch = gitx.try_text(main_dir, "symbolic-ref", "--short", "-q", "HEAD").strip()
    names = gitx.try_text(main_dir, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    return branch, sorted(n for n in names if not n.startswith(JOB_BRANCH_PREFIX))


def warn_branch_changes(before, after, files=()):
    """Warn about a main-checkout branch switch or new branch during the run (a test left the checkout on another branch). `files`: the test files whose own run saw the change. -> True when the state changed or a file saw it."""
    if before == after and not files:
        return False
    (old_branch, old_names), (new_branch, new_names) = before, after
    new = sorted(set(new_names) - set(old_names))
    print(f"ptest: ERROR: main checkout branch changed during this run: {old_branch or '(none)'} -> {new_branch or '(none)'}")
    if files:
        print(f"  seen during test files: {', '.join(files)}")
    if new:
        print(f"  new branches: {', '.join(new)}")
    return True


def run_guarded(tests_dir, name, cov_dir=None):
    """run_file plus the main checkout branch state before and after it -> (run_file result, name when the branch state moved during it, else None)."""
    before = main_branch_state()
    res = run_file(tests_dir, name, cov_dir)
    return res, (name if before != main_branch_state() else None)


def run_all(tests_dir, names, cov_dir, jobs):
    """-> (results sorted, names whose run window saw a main checkout branch switch: parallel runs make every overlapping file one)."""
    with ThreadPoolExecutor(jobs) as pool:
        guarded = list(pool.map(lambda n: run_guarded(tests_dir, n, cov_dir), names))
    return sorted((r for r, _ in guarded), reverse=True), [n for _, n in guarded if n]


def summary_line(names, bad, skipped, elapsed):
    """The closing line: failing file names (if any), file count, failed count, skipped count, elapsed seconds."""
    failed_names = f"FAILED {', '.join(r[2] for r in bad)}: " if bad else ""
    return f"{failed_names}{len(names)} files, {len(bad)} failed, {skipped} tests skipped, {elapsed:.1f}s"


def new_cov_dir(enabled):
    """Scratch dir for the per-file coverage data, with its rc file written; None when coverage is off."""
    if not enabled:
        return None
    cov_dir = tempfile.mkdtemp(prefix="ptest-cov-")
    cov_rc(cov_dir)
    return cov_dir


def skips_fail_run(skipped):
    """True when skipped tests fail the run: CI=1 in the env and any test skipped (the message says so)."""
    if not skipped or os.environ.get(ENV_FAIL_ON_SKIP) != "1":
        return False
    print(f"ptest: {skipped} skipped tests fail the run ({ENV_FAIL_ON_SKIP}=1)")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=min(32, os.cpu_count() or 4))
    ap.add_argument("--dir", default=os.path.join(HERE, "tests"))
    ap.add_argument("--cov", action="store_true", help="measure coverage (parallel data files, combined, reported against pyproject fail_under)")
    ap.add_argument("patterns", nargs="*", default=["test_*.py"])
    a = ap.parse_args(argv)
    names = select_files(a.dir, a.patterns, merge_only_tests())
    missing = unmatched_patterns(a.dir, a.patterns)
    if missing or not names:
        print(f"ptest: no test files match: {' '.join(missing or a.patterns)}")
        return 1
    start = time.time()
    claude_before = main_claude_status()
    branch_before = main_branch_state()
    cov_dir = new_cov_dir(a.cov)
    res, switched = run_all(a.dir, names, cov_dir, a.jobs)
    print_slowest(res)
    bad = [r for r in res if r[1]]
    print_failures(bad)
    skipped = count_skipped(res)
    print(summary_line(names, bad, skipped, time.time() - start))
    warn_new_claude_changes(claude_before, main_claude_status())  # warning only: never changes the exit code
    branch_moved = warn_branch_changes(branch_before, main_branch_state(), switched)  # a switched branch fails the run: it blocks merge-w3
    skip_failed = skips_fail_run(skipped)
    cov_rc_failed = report_cov(cov_dir) if cov_dir else 0
    return 1 if bad or skip_failed or cov_rc_failed or branch_moved else 0


if __name__ == "__main__":
    sys.exit(main())
