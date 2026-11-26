"""retry of merge-w{N} also resets the fix-gate devs and the g-merges of that wave."""

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import cli_orch  # noqa: E402
from pipeline.steps import history  # noqa: E402

IDS = ("merge-w2", "fix-gate-w2", "merge-w2-g1", "fix-gate-w2-2", "merge-w2-g2")


class RetryMergeChain(unittest.TestCase):
    def test_fix_chain_reset(self):
        t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, t, True)
        os.makedirs(f"{t}/.work/s")
        ev = [{"ev": "report", "task": x, "id": x, "role": "script", "verdict": "FAIL", "file": "old.md"} for x in IDS]
        with open(f"{t}/.work/s/orch.jsonl", "w") as f:
            f.write("".join(json.dumps(e) + "\n" for e in ev))
        a = argparse.Namespace(cmd="retry", stage="s", dir=t, step="merge-w2", note="", from_step="")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_orch.cmd_reset(a)
        with open(f"{t}/.work/s/orch.jsonl") as f:
            evs = [json.loads(x) for x in f if x.strip()]
        for sid in IDS:
            self.assertEqual([e for e in history(evs, sid) if e["ev"] == "report"], [], sid)


if __name__ == "__main__":
    unittest.main()
