"""denials: family labels and a synthetic transcript scan."""

import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import denials
from tests.support.tmpdirs import tracked_mkdtemp

SNIP = '"/usr/local/bin/snip" run -- '
MULTI = "This Bash command contains multiple operations. The following part requires approval: "


class Family(unittest.TestCase):
    def test_labels(self):
        f = denials.family
        self.assertEqual(f("This command changes directory before running a version-control command, which can pick up untrusted hooks...", ""), "cd-then-git")
        self.assertEqual(f(MULTI + "cd /w/x && " + SNIP + "git -c safe.directory='*' status", ""), "multi: cd&&git")
        self.assertEqual(f(MULTI + "cd /w", ""), "multi: cd&&other")
        self.assertEqual(f(MULTI + SNIP + "git status", ""), "multi: git part")
        self.assertEqual(f(MULTI + "make all", ""), "multi: other (make all)")
        self.assertEqual(f("This command requires approval", "rm -rf x"), "approval: rm")
        self.assertEqual(f("This command requires approval", "FOO=1 rm x"), "approval: ENV-prefix rm")
        self.assertEqual(f("sed command requires approval (contains potentially dangerous operations)", ""), "sed")
        self.assertEqual(f("No pipe allowed", ""), "qrun-chain hook")
        self.assertEqual(f("Reviewer sessions may not", ""), "reviewer guard")
        self.assertEqual(f("blocked: redirection", ""), "cd+redirect")
        self.assertEqual(f("weird blocked thing", ""), "other: weird blocked thing")


class Scan(unittest.TestCase):
    def test_scan(self):
        d = tracked_mkdtemp()
        os.mkdir(os.path.join(d, "p"))
        ts = datetime.date.today().isoformat() + "T10:00:00.000Z"

        def use(i, cmd, entry="sdk-cli"):
            blk = {"type": "tool_use", "name": "Bash", "id": i, "input": {"command": cmd}}
            return {"entrypoint": entry, "timestamp": ts, "message": {"content": [blk]}}

        def res(i, text, err=True):
            blk = {"type": "tool_result", "tool_use_id": i, "is_error": err, "content": text}
            return {"entrypoint": "sdk-cli", "timestamp": ts, "message": {"content": [blk]}}

        evs = [
            use("1", "rm x"),
            res("1", "This command requires approval"),
            use("2", "ls"),
            res("2", [{"text": "ok"}], False),
            use("3", "sed -i x"),
            res("3", [{"text": "sed command requires approval"}]),
        ]
        with open(os.path.join(d, "p", "a.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(e) for e in evs) + "\nnot json\n")
        with open(os.path.join(d, "p", "b.jsonl"), "w") as f:
            f.write(json.dumps(use("9", "ls", "cli")) + "\n")
        calls, den = denials.collect(d, 5)
        s = denials.summarize(calls, den)
        self.assertEqual((s["bash_calls"], s["denied"]), (3, 2))
        self.assertEqual({x["family"] for x in s["families"]}, {"approval: rm", "sed"})
        self.assertIn("denied: 2", denials.render(s, 1))


if __name__ == "__main__":
    unittest.main()
