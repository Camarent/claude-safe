import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "localmodel"))
import agent_files as af
import explore as ex
from tests.support.tmpdirs import tracked_mkdtemp


def make_repo():
    d = tracked_mkdtemp()
    for a in (["init", "-q", "-b", "master"],):
        subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)
    with open(os.path.join(d, "a.py"), "w") as fh:
        fh.write("x = 1\n")
    subprocess.run(["git", "-C", d, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True, capture_output=True)
    return d


class Explore(unittest.TestCase):
    def test_missing_repo_gives_none(self):
        self.assertIsNone(ex.explore({"r": "/no/such/dir"}, "req"))

    def test_explore_reads_the_live_tree_and_clears_boxes(self):
        d = make_repo()
        seen = []

        def post(msgs, tools=True):
            seen.append(msgs)
            if len(seen) == 1:
                return {"content": [{"type": "tool_use", "id": "1", "name": "bash", "input": {"repo": "r", "cmd": "rm a.py"}}]}
            return {
                "content": [{"type": "text", "text": json.dumps({"code": [{"repo": "r", "path": "a.py", "why": "w"}], "tests": [], "docs": [], "data": []})}]
            }

        got = ex.explore({"r": d}, "req", post=post)
        self.assertEqual(got, [("r", "a.py", "w", "code")])
        self.assertTrue(os.path.isfile(os.path.join(d, "a.py")))
        self.assertEqual(af.BOXES, {})
        self.assertEqual(subprocess.run(["git", "-C", d, "worktree", "list"], capture_output=True, text=True, check=False).stdout.count("\n"), 1)
        refused = seen[1][-1]["content"][0]["content"]
        self.assertTrue(refused.startswith("error"))
        self.assertEqual(af.STATS["tool_errors"], 1)
        self.assertTrue(af.STATS["first_tool_error"].startswith("error"))


if __name__ == "__main__":
    unittest.main()
