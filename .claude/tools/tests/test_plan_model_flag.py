"""cs-plan run --model: default Sonnet, opus starts the architect on Opus (tag plan-opus), no Sonnet pass."""

import argparse
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan, plan_run
from tests.support.tmpdirs import tracked_mkdtemp


class PlanModelFlagTest(unittest.TestCase):
    def parse(self, *extra):
        return cli_plan.build().parse_args(["run", "--feature", "f", "--prompt-file", "p", *extra])

    def test_default_is_sonnet_and_rejects_haiku(self):
        self.assertEqual(self.parse().model, "sonnet")
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            self.parse("--model", "haiku")

    def test_opus_runs_one_opus_session(self):
        d = tracked_mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        pf = os.path.join(d, "p")
        open(pf, "w").write("goal")
        a = argparse.Namespace(dir=d, feature="f", prompt_file=pf, dry_run=False, model="opus")
        seen = []
        with (
            mock.patch.object(plan_run, "session", side_effect=lambda a, c, m, p, tag: seen.append((m, tag)) or "DONE"),
            mock.patch.object(plan_run, "code_context", return_value=""),
            mock.patch.object(plan_run, "check_now", return_value=(3, [])),
            mock.patch.object(plan_run, "step"),
            mock.patch("builtins.print"),
        ):
            rc = plan_run.run(a)
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [("opus", "plan-opus")])


if __name__ == "__main__":
    unittest.main()
