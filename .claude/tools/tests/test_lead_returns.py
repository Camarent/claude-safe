"""Synthetic tests: situations that used to send the stage to the lead and now are handled first (merge failure, ops warnings, a dead driver)."""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_wave
from pipeline.ctx import Ctx
from tests.support.orch_harness import OrchHarness


class LeadReturns(OrchHarness):
    def flags(self, text):
        with open(f"{self.d}/.work/s/TASKS.md", "w") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n## Graph\nwave 1: task-1\nflags: "
                + text
                + "\n"
            )

    def to_merge(self):
        self.answer(self.next(), "VERDICT: DONE")
        self.answer(self.next(), "VERDICT: PASS")
        m = self.next()
        self.assertEqual(m["agent"], "merger")
        return m

    def test_merge_failure_gets_a_sonnet_fix_and_a_second_merge(self):
        self.flags("no-test no-audit no-retro no-index no-clean no-push no-live no-release")
        self.answer(self.to_merge(), "VERDICT: FAIL\nconflict in a.py")
        fix = self.next()
        self.assertEqual((fix["agent"], fix["model"]), ("merger", "sonnet"))
        self.assertIn("report-a3", fix["prompt"])
        self.answer(fix, "VERDICT: DONE")
        m2 = self.next()
        self.assertEqual(m2["agent"], "merger")
        self.answer(m2, "VERDICT: DONE")
        self.assertEqual(self.go("next")["action"], "done")

    def test_post_publish_ops_failures_are_warnings(self):
        self.flags("no-test no-audit no-retro no-push no-live")  # index and clean stay on
        self.answer(self.to_merge(), "VERDICT: DONE")
        self.assertIn("master-merged", self.go("next")["message"])
        self.go("skip", "--step", "master-merged")
        idx = self.next()
        self.assertIn("index", idx["prompt"])
        self.answer(idx, "VERDICT: FAIL\nno server")
        cl = self.next()
        self.assertIn("cleanup", cl["prompt"].lower())
        self.answer(cl, "VERDICT: FAIL\nlocked file")
        self.assertEqual(self.go("next")["action"], "done")

    def test_a_dead_driver_gets_one_automatic_rerun_per_step(self):
        self.flags("no-test no-audit no-retro no-index no-clean no-push no-live no-release")
        first = self.next()
        self.assertEqual(self.go("next")["action"], "ask_main")  # dispatched, never reported
        ctx = Ctx(SimpleNamespace(stage="s", dir=self.d))
        self.assertEqual(cli_wave.recover_unreported(ctx), 1)
        again = self.next()
        self.assertEqual(again["agent"], first["agent"])
        self.assertNotEqual(again["id"], first["id"])
        self.assertEqual(self.go("next")["action"], "ask_main")  # dies again: now the lead decides, no endless loop
        self.assertEqual(cli_wave.recover_unreported(ctx), 0)

    def test_a_dead_reviewer_rerun_does_not_restart_the_dev(self):
        self.flags("no-test no-audit no-retro no-index no-clean no-push no-live no-release")
        self.answer(self.next(), "VERDICT: DONE")  # dev finished
        rev = self.next()
        self.assertEqual(rev["agent"], "reviewer")
        self.assertEqual(self.go("next")["action"], "ask_main")  # reviewer dispatched, never reported: driver died
        ctx = Ctx(SimpleNamespace(stage="s", dir=self.d))
        self.assertEqual(cli_wave.recover_unreported(ctx), 1)
        again = self.next()
        self.assertEqual(again["agent"], "reviewer")  # the re-run is the reviewer, not a new dev run over the finished work
        self.assertNotEqual(again["agent"], "dev")


if __name__ == "__main__":
    unittest.main()
