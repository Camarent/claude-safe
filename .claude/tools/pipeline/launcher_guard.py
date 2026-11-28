"""Refuse a bare `cs-wave run` in a repo that names its launcher (pipeline.json key LAUNCHER_KEY).

The launcher (scripts/pipeline_auto.py) sets the auto permission flags in ENV_CLAUDE_FLAGS before it starts cs-wave. A run
started without them would use the default mode, so the guard says so and names the launcher to use instead. A repo without
the key, a preview run (--dry-run) and the status and metrics commands are never refused.
"""

from .configuration import ENV_CLAUDE_FLAGS, LAUNCHER_KEY, PERMISSION_MODE_AUTO, PERMISSION_MODE_FLAG

RUN_CMD = "run"  # the cs-wave subcommand the guard checks


def check(cfg, environ, cmd, preview) -> str | None:
    """-> the refusal message, or None when the run may go on: the launcher key is unset, cmd is not `run`, the run is a preview, or the auto flags are in environ[ENV_CLAUDE_FLAGS]."""
    launcher = cfg.get(LAUNCHER_KEY, "")
    if not launcher or cmd != RUN_CMD or preview:
        return None
    if f"{PERMISSION_MODE_FLAG} {PERMISSION_MODE_AUTO}" in environ.get(ENV_CLAUDE_FLAGS, ""):
        return None
    return f"refused: this repo starts runs through its launcher, which sets the auto permission flags. Use: {launcher} run <name>"
