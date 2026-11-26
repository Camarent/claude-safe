"""Start a planned stage or feature in auto permission mode (opt-in).

Sets the headless-session flags to the full default flags (allowlists kept) with the permission mode switched to auto (the default mode is replaced), then execs
`cs-wave <args>`. The args must start with `feature` or `run` (`cs-wave feature ...`, `cs-wave run --stage ...`).

    python3 -I scripts/pipeline_auto.py feature --feature F
    python3 -I scripts/pipeline_auto.py run --stage S
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# bootstrap only: the tools folder cannot come from pipeline.configuration, which is the module this line makes importable (as scripts/host_agents.py does)
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
from pipeline.configuration import (  # noqa: E402  (the sys.path line above must run first)
    CS_WAVE,
    DEFAULT_FLAGS,
    ENV_CLAUDE_FLAGS,
    PERMISSION_MODE_AUTO,
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_FLAG,
    WAVE_MODES,
)


def auto_flags(base=DEFAULT_FLAGS):
    """-> the full default flags (BASE_FLAGS plus the Bash allowlist and other allows) with the permission mode replaced by auto."""
    default = f"{PERMISSION_MODE_FLAG} {PERMISSION_MODE_DEFAULT}"
    if default not in base:
        raise ValueError(f"BASE_FLAGS have no {default!r}; cannot switch to auto mode")
    return base.replace(default, f"{PERMISSION_MODE_FLAG} {PERMISSION_MODE_AUTO}", 1)


def plan(args, environ):
    """-> (argv, env) for the cs-wave call; raises ValueError on a bad first word."""
    if not args or args[0] not in WAVE_MODES:
        raise ValueError(f"first argument must be one of {', '.join(WAVE_MODES)}")
    env = dict(environ)
    env[ENV_CLAUDE_FLAGS] = auto_flags()
    return [CS_WAVE, *args], env


def main(argv=None, environ=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        cmd, env = plan(args, os.environ if environ is None else environ)
    except ValueError as exc:
        print(f"pipeline_auto: {exc}", file=sys.stderr)
        return 2
    os.execve(cmd[0], cmd, env)  # replaces this process; returns only on error (raises OSError)


if __name__ == "__main__":
    sys.exit(main())
