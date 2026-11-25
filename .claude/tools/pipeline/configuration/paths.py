"""Pipeline home and tool paths, the command index (`cs-*` names) and the qrun wrapper patterns."""

import os
import re

from pipeline import cmdnames

# Where the pipeline lives (the folder with tools/, agents/, practices/). Derived from this file, so a clone, a worktree and the
# container (/root/.claude) all work without edits; env PIPELINE_HOME overrides. Never write /root/.claude as a literal elsewhere.
HOME = os.environ.get("PIPELINE_HOME") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TOOLS_DIR = os.path.join(HOME, "tools")
CS_WAVE = os.path.join(HOME, "bin", "cs-wave")  # the stage/feature driver entry point (scripts/pipeline_auto.py execs it)
WAVE_MODES = ("feature", "run")  # cs-wave subcommands the auto-mode launcher starts
ASK_VENV_PY = "/opt/ask-venv/bin/python"  # the embedding library's own venv (env ASK_CODE_PY overrides)
VERDICT_PREFIX = "VERDICT: "  # first line of a step's output: the driver reads the verdict word after it
AGENTS_DIR = os.path.join(HOME, "agents")
PRACTICES_DIR = os.path.join(HOME, "practices")
GUIDELINES_DIR = os.path.join(PRACTICES_DIR, "guidelines")  # guidelines.py / prepare_repo.py: one <lang>.md per language plus base.md


def tool_cmd(script):
    """Command prefix that runs a pipeline tool in isolated mode: tool_cmd("qrun.py") -> "python3 -I <tools>/qrun.py"."""
    return f"python3 -I {os.path.join(TOOLS_DIR, script)}"


def _cs_index():
    """The command index of this home, or None when paths.json is missing or unreadable (matchers then fall back to the python form)."""
    try:
        return cmdnames.load(HOME)
    except (OSError, ValueError):
        return None


def cs_names(index=None):
    """Every container-side `cs-*` name (prefix from the index) plus its aliases that carry the prefix; derived from cmdnames.names_for."""
    index = index or _cs_index()
    if not index:
        return ()
    keys = cmdnames.commands(index, "container")
    return tuple(dict.fromkeys(n for k in keys for n in cmdnames.names_for(index, k) if n.startswith(index["prefix"])))


QRUN_WRAPPER = next((n for n in cs_names() if n.endswith("qrun")), "cs-qrun")  # the wrapper name of qrun.py
QRUN_PATTERN = (  # head of a qrun call: `python3 -I .../qrun.py`, a bare path to it, or the wrapper
    rf"(?:(?:python3?\s+(?:-\w+\s+)*)?[\"']?\S*qrun\.py[\"']?|(?:\S*/)?{re.escape(QRUN_WRAPPER)})"
)


def allowed(*words):
    """Verdict list as written in a report instruction: allowed(DONE, FAIL) -> "DONE|FAIL"."""
    return "|".join(words)
