"""plan_check rule: a final gate command that runs a script whose header lists gate modes, but names no mode flag (so it runs the full mode)."""

import os
import shlex

from audit import final_check

from .configuration import GATE_MODE_FULL, GATE_MODE_PREFIX, project
from .gate_mode import header_modes, mode_of
from .util import read_text


def _script(cmd, repo):
    """-> the path of the script the command CMD runs first (its first word), when that file is in REPO; else None."""
    try:
        words = shlex.split(cmd)
    except ValueError:
        return None
    if not words:
        return None
    path = os.path.join(repo, words[0])
    return path if os.path.isfile(path) else None


def check_gate_modes(workspace, repo) -> list[str]:
    """`gatemode:` problems: a final gate command of REPO whose script header lists mode flags while the command names none (mode_of is the full mode); the problem names the command and the documented modes."""
    cfg = project(workspace)
    problems: dict[str, None] = {}
    for cmd in final_check.commands(cfg, repo, merge=True, final=True):
        path = _script(cmd, repo)
        if path is None:
            continue
        modes = header_modes(read_text(path))
        if modes and mode_of(cmd) == GATE_MODE_FULL:
            problems[
                f"{GATE_MODE_PREFIX} gate `{cmd}` names no mode flag and runs the full mode; {os.path.basename(path)} documents {', '.join(sorted(modes))}: name one of them in the command"
            ] = None
    return list(problems)
