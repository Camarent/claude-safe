"""One `git` helper for tests that build a throwaway repo."""

import subprocess


def git(d, *args, check=True):
    """Run `git -C d args` with a fixed identity; -> stripped stdout. check=False tolerates a failing command (the output is then often empty)."""
    return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True, check=check).stdout.strip()
