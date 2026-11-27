#!/usr/bin/env python3
"""cs-devtools: run one tool of the AUDIT.md audit-tools block over the given paths of the current worktree.

Usage: cs-devtools <tool> [PATH...]   (PATH relative to the worktree, appended to the tool's own command)
Exit: 0 clean, 1 findings, 2 refused (tool not in the block) or the tool could not run. Runs through audit_run.
"""

import os
import shlex
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_run

USAGE = "usage: cs-devtools <tool> [PATH...]"


def main(argv=None):
    """Run the audit tool named in argv[0] over the paths after it; -> exit code."""
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(USAGE, file=sys.stderr)
        return 2
    name, paths = args[0], args[1:]
    repo = os.getcwd()
    tools = {t[0]: t for t in audit_run.load_tools(repo)}
    if name not in tools:
        print(f"REFUSED: {name} is not in the audit-tools block of AUDIT.md (known: {', '.join(sorted(tools)) or 'none'})", file=sys.stderr)
        return 2
    _, cwd, cmd, parser = tools[name]
    cmd = " ".join([cmd] + [shlex.quote(p) for p in paths])
    stamp = audit_run._stamp(repo)
    rawdir = os.path.join(repo, ".audits", "raw")
    os.makedirs(rawdir, exist_ok=True)
    _, _, code = audit_run._run_tools(repo, [(name, cwd, cmd, parser)], SimpleNamespace(verbose=False), stamp, rawdir)
    return code


if __name__ == "__main__":
    sys.exit(main())
