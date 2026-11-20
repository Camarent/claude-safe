"""No NEW raw `["git"` subprocess use: git goes through pipeline/gitx.py. RAW_ALLOWED lists the files still raw (backlog R7); remove an entry when its file is migrated."""

import os
import re
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_ALLOWED = {
    "commit.py",  # the commit must show hook output on the terminal: gitx.run captures it
    "pipeline/configuration/gitbranch.py",  # gitx imports configuration: import cycle
    "ptest.py",  # runs standalone (python3 -I, no package import, release container): its one git call for the .claude/ warning is raw
}


def raw_git_files():
    found = set()
    for root, dirs, files in os.walk(TOOLS):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__")]
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, TOOLS)
            if f.endswith(".py") and rel != "pipeline/gitx.py":
                with open(p, encoding="utf-8", errors="replace") as fh:
                    if re.search(r'\["git"', fh.read()):
                        found.add(rel)
    return found


class NoRawGit(unittest.TestCase):
    def test_no_new_raw_git(self):
        self.assertEqual(sorted(raw_git_files() - RAW_ALLOWED), [], "use pipeline.gitx (run / out / try_out)")

    def test_allow_list_has_no_stale_entry(self):
        self.assertEqual(sorted(RAW_ALLOWED - raw_git_files()), [], "migrated: remove from RAW_ALLOWED")


if __name__ == "__main__":
    unittest.main()
