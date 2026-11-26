"""ops_steps digest and qrun log write through pipeline.util.write_text: a failure mid-write keeps the earlier file intact."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ops_steps
import qrun
from tests.support.tmpdirs import tracked_mkdtemp

FAIL_MID_WRITE = "pipeline.util.os.fsync"  # write_text fsyncs the temp file before the rename


def _session(cost):
    return {"ev": "session", "agent": "dev", "model": "haiku", "task": "alpha", "verdict": "PASS", "cost_usd": cost}


class DigestWriteTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = os.path.realpath(t.name)
        self.root = os.path.join(self.d, ".work", "s")
        os.makedirs(self.root)

    def write_metrics(self, rows):
        with open(os.path.join(self.root, "metrics.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))

    def test_failed_write_keeps_the_earlier_digest(self):
        digest_file = os.path.join(self.root, "retro", "retro-digest.md")
        self.write_metrics([_session(0.5)])
        self.assertTrue(ops_steps.digest(self.d, "s")[0])
        with open(digest_file, encoding="utf-8") as fh:
            before = fh.read()
        self.write_metrics([_session(9.25), _session(1.0)])
        with mock.patch(FAIL_MID_WRITE, side_effect=OSError("disk full")), self.assertRaises(OSError):
            ops_steps.digest(self.d, "s")
        with open(digest_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)


class QrunLogWriteTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.log = os.path.join(self.d, "l.log")

    def test_failed_write_keeps_the_earlier_log(self):
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write("earlier run\n")
        cmd = [sys.executable, "-I", "-c", "print('new run')"]
        # the subreaper is a process-wide prctl; the test must not set it on the runner
        with mock.patch.object(qrun, "_become_subreaper"), mock.patch(FAIL_MID_WRITE, side_effect=OSError("disk full")), self.assertRaises(OSError):
            qrun._run(cmd, self.log)
        with open(self.log, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "earlier run\n")


if __name__ == "__main__":
    unittest.main()
