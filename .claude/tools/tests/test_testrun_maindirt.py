"""Main-checkout dirt stops a task as BLOCKED (the lead clears it); a dev's own link write stays FAIL."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testrun
import wt
from audit import taskgates
from pipeline.configuration import BLOCKED, FAIL
from pipeline.configuration.limits import MAIN_WRITE_NOTE
from tests.support.tmpdirs import tracked_mkdtemp

MAIN_NOTE = MAIN_WRITE_NOTE.format(files="f (mtime t, job start t)", more=0)
LINK_NOTE = "FAIL: path outside this task's worktree: x"


def box():
    d = tracked_mkdtemp()
    os.makedirs(f"{d}/.work/s")
    with open(f"{d}/.work/s/task-1.md", "w") as f:
        f.write("# t\nTest: true\n")
    with open(f"{d}/pipeline.json", "w") as f:
        json.dump({"worktrees": True}, f)
    return d


class OutsideVerdictTest(unittest.TestCase):
    def test_main_dirt_is_blocked(self):
        self.assertEqual(testrun.outside_verdict(MAIN_NOTE), (BLOCKED, MAIN_NOTE))

    def test_link_write_is_fail(self):
        self.assertEqual(testrun.outside_verdict(LINK_NOTE), (FAIL, LINK_NOTE))

    def test_main_dirt_with_link_write_is_blocked(self):
        note = "\n".join([MAIN_NOTE, LINK_NOTE])
        self.assertEqual(testrun.outside_verdict(note)[0], BLOCKED)


class RunMainDirtTest(unittest.TestCase):
    def setUp(self):
        self.d = box()
        p = mock.patch.object(wt, "live_job", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def run_with(self, main, link):
        with (
            mock.patch.object(testrun.maincheck, "check", return_value=main),
            mock.patch.object(testrun.linkguard, "check", return_value=link),
        ):
            return testrun.run("s", "task-1", self.d, runner=mock.Mock())

    def test_main_dirt_blocks(self):
        v, note = self.run_with(MAIN_NOTE, "")
        self.assertEqual(v, BLOCKED)
        self.assertIn("git -C <main checkout> status", note)

    def test_linkwrite_still_fails(self):
        v, note = self.run_with("", LINK_NOTE)
        self.assertEqual((v, note), (FAIL, LINK_NOTE))


class TaskgatesMainDirtTest(unittest.TestCase):
    def setUp(self):
        self.d = box()
        p = mock.patch.object(wt, "live_job", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_taskgates_main_dirt_blocks(self):
        runner = mock.Mock()
        with (
            mock.patch.object(taskgates.testrun, "workdir", return_value=self.d),
            mock.patch.object(taskgates.final_check, "commands", return_value=["mylint"]),
            mock.patch.object(taskgates.testrun.maincheck, "check", return_value=MAIN_NOTE),
            mock.patch.object(taskgates.testrun.linkguard, "check", return_value=""),
        ):
            v, text = taskgates.run("s", "task-1", self.d, runner=runner)
        self.assertEqual(v, BLOCKED)
        self.assertIn("MAIN-DIRT:", text)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
