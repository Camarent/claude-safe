"""Env preflight for a stage: the `Needs:` and `Mode:` lines of the task files, probed once each before the stage runs.
`needs_lines` and `mode_lines` parse a task text. `run` probes every need (binary on PATH, repo file, gh token scope) and
the scratch TMPDIR length, and writes ALL problems to one BLOCKED.md in the stage dir. A probe that cannot tell (gh missing,
gh failing, a fine-grained token with no X-OAuth-Scopes header) is unverifiable and never a problem. Probes take injectable
runner/which/env, so tests use fakes and touch no network."""

import glob
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence

from .configuration import GIT_TIMEOUT_S, QRUN_TMP_BASE, QRUN_TMP_MAX_PATH, QRUN_TMP_PREFIX

NEEDS_RE = re.compile(r"^Needs:[ \t]*(.+?)[ \t]*$", re.M)
MODE_RE = re.compile(r"^Mode:[ \t]*(\S+)[ \t]+([0-7]{3,4})[ \t]*$", re.M)
TASK_GLOB = "task-*.md"
BLOCKED_FILE = "BLOCKED.md"
GH_TOOL = "gh"
GH_SCOPE_ARGV = (GH_TOOL, "api", "-i", "/user")
SCOPE_HEADER = "x-oauth-scopes"
MKTEMP_SUFFIX_LEN = 8  # tempfile.mkdtemp appends 8 random characters to the prefix
TMP_FIX = "export QRUN_TMP_BASE=/tmp"


def needs_lines(task_text: str) -> list[tuple[str, str]]:
    """Task text -> [(kind, value)] from each `Needs: bin:<name>, file:<repo-path>, scope:<gh scope>` line, in order."""
    out = []
    for m in NEEDS_RE.finditer(task_text):
        for item in m.group(1).split(","):
            kind, sep, value = item.strip().partition(":")
            if sep and kind in ("bin", "file", "scope") and value:
                out.append((kind, value))
    return out


def mode_lines(task_text: str) -> list[tuple[str, str]]:
    """Task text -> [(repo-path, mode)] from each `Mode: <repo-path> 755` line, in order."""
    return [(m.group(1), m.group(2)) for m in MODE_RE.finditer(task_text)]


def _default_runner(argv):
    """argv -> (returncode, stdout); a missing program or a timeout is (None, '')."""
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return r.returncode, r.stdout


def _scope_problem(scope, runner, which):
    """gh token scope probe -> None when the scope is granted or cannot be verified, else a problem tuple."""
    if which(GH_TOOL) is None:
        return None  # gh missing: unverifiable
    rc, out = runner(GH_SCOPE_ARGV)
    if rc != 0:
        return None  # gh failing: unverifiable
    for line in out.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == SCOPE_HEADER:
            granted = {s.strip() for s in value.split(",") if s.strip()}
            if scope in granted:
                return None
            return (f"gh token lacks scope {scope}", f"gh auth refresh -s {scope}")
    return None  # no X-OAuth-Scopes header: a fine-grained token, scope unverifiable


def _tmp_problem(env):
    """Scratch TMPDIR probe: the path qrun would create under QRUN_TMP_BASE longer than QRUN_TMP_MAX_PATH -> problem tuple."""
    base = env.get("QRUN_TMP_BASE", QRUN_TMP_BASE)
    sample = os.path.join(base, QRUN_TMP_PREFIX + "x" * MKTEMP_SUFFIX_LEN)
    if len(sample) > QRUN_TMP_MAX_PATH:
        return (f"scratch TMPDIR {sample} is {len(sample)} bytes, over {QRUN_TMP_MAX_PATH}", TMP_FIX)
    return None


def _probe_need(kind, value, repo, runner, which):
    """One need -> problem tuple (message, fix) or None."""
    if kind == "bin":
        if which(value) is None:
            return (f"binary not on PATH: {value}", f"install {value} and put it on PATH")
        return None
    if kind == "file":
        if not os.path.exists(os.path.join(repo, value)):
            return (f"file missing in repo: {value}", f"create {value} in {repo}")
        return None
    return _scope_problem(value, runner, which)


def run(
    repo: str,
    stage_dir: str,
    runner: Callable[[Sequence[str]], tuple[int | None, str]] = _default_runner,
    which: Callable[[str], str | None] = shutil.which,
    env: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Probe the stage's needs and the scratch TMPDIR -> [(message, fix)]; writes all of them to stage_dir/BLOCKED.md.
    Needs come from every task-*.md in stage_dir, each probed once. No problems: no file written."""
    env = os.environ if env is None else env
    texts = []
    for path in sorted(glob.glob(os.path.join(stage_dir, TASK_GLOB))):
        with open(path, encoding="utf-8") as fh:
            texts.append(fh.read())
    needs = list(dict.fromkeys(n for text in texts for n in needs_lines(text)))
    problems = [p for p in (_probe_need(k, v, repo, runner, which) for k, v in needs) if p]
    tmp = _tmp_problem(env)
    if tmp:
        problems.append(tmp)
    if problems:
        body = "".join(f"- {msg}\n  fix: {fix}\n" for msg, fix in problems)
        os.makedirs(stage_dir, exist_ok=True)
        with open(os.path.join(stage_dir, BLOCKED_FILE), "w", encoding="utf-8") as fh:
            fh.write("# Env preflight: blocked\n\n" + body)
    return problems
