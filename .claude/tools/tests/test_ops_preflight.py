"""Open ops steps: a manual step with no skip event and no task dependency blocks the stage once, before any dispatch; a skip lifts it; a step that waits for a task only shows as a later lead step."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_wave, ops_preflight
from pipeline.configuration import BLOCKED_FILE, EV_SKIP
from pipeline.util import append_event, paths
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

FIRST_TASK = "task-1"
SECOND_TASK = "task-2"

TASKS = f"""# Stage: s
- {FIRST_TASK} first dev task
- ops-1 manual: sign the release key
- {SECOND_TASK} second dev task
  Depends: ops-1
- ops-2 manual: check the mirror cert
  Depends: {FIRST_TASK}
"""


class OpsPreflightTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.w = f"{self.d}/.work/s"
        os.makedirs(self.w)
        Path(self.w, "TASKS.md").write_text(TASKS, encoding="utf-8")
        self.c = SimpleNamespace(w=self.w, log=paths(SimpleNamespace(dir=self.d, stage="s"))[1])
        self.a = SimpleNamespace(dir=self.d, stage="s")

    def skip(self, step):
        append_event(self.c.log, {"ev": EV_SKIP, "task": step})

    def test_open_ops_lists_the_step_no_task_waits_for_with_its_dependents(self):
        steps = ops_preflight.open_ops(self.a, self.c)
        self.assertEqual([s["id"] for s in steps], ["ops-1"])
        self.assertEqual(steps[0]["text"], "sign the release key")
        self.assertEqual(steps[0]["dependents"], [SECOND_TASK])
        self.assertIn("orch.py", steps[0]["skip"])
        self.assertIn("skip --step ops-1", steps[0]["skip"])

    def test_later_ops_lists_the_step_that_waits_for_a_task(self):
        later = ops_preflight.later_ops(self.c)
        self.assertEqual([(s["id"], s["after"]) for s in later], [("ops-2", [FIRST_TASK])])

    def test_blocked_text_names_step_text_dependents_and_skip(self):
        text = ops_preflight.blocked_text(ops_preflight.open_ops(self.a, self.c))
        self.assertIn("- ops-1: sign the release key", text)
        self.assertIn(f"dependents: {SECOND_TASK}", text)
        self.assertIn("skip --step ops-1", text)

    def test_skipped_step_is_no_longer_listed(self):
        self.skip("ops-1")
        self.assertEqual(ops_preflight.open_ops(self.a, self.c), [])

    def test_mutation_counting_a_skipped_step_as_open_would_fail(self):
        # the skip test above is the guard: with the skip event ignored the step would be listed again
        self.skip("ops-1")
        with mock.patch.object(ops_preflight, "read_jsonl", return_value=[]):
            self.assertEqual([s["id"] for s in ops_preflight.open_ops(self.a, self.c)], ["ops-1"])
        self.assertEqual(ops_preflight.open_ops(self.a, self.c), [])


class OpsPreflightRunTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.w = f"{self.d}/.work/s"
        os.makedirs(self.w)
        Path(self.w, "TASKS.md").write_text(TASKS, encoding="utf-8")
        self.log = paths(SimpleNamespace(dir=self.d, stage="s"))[1]

    def run_w(self, loop_rc=0):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli_wave, "run_loop", return_value=loop_rc) as loop:
            rc = cli_wave.main(["run", "--stage", "s", "--dir", self.d])
        return rc, out.getvalue(), loop

    def test_open_step_blocks_once_before_any_dispatch(self):
        rc, out, loop = self.run_w()
        self.assertEqual(rc, 4)
        loop.assert_not_called()
        self.assertIn("blocked: manual step ops-1 is open: sign the release key", out)
        self.assertNotIn("blocked: manual step ops-2", out)
        blocked = Path(self.w, BLOCKED_FILE).read_text(encoding="utf-8")
        self.assertIn("- ops-1: sign the release key", blocked)
        self.assertIn(f"dependents: {SECOND_TASK}", blocked)
        self.assertNotIn("- ops-2", blocked)

    def test_skip_lifts_the_block_and_the_run_goes_on(self):
        append_event(self.log, {"ev": EV_SKIP, "task": "ops-1"})
        rc, out, loop = self.run_w()
        self.assertEqual(rc, 0)
        loop.assert_called_once()
        self.assertNotIn("blocked: manual step", out)
        self.assertIn(f"lead step ops-2 (after {FIRST_TASK}): check the mirror cert", out)


if __name__ == "__main__":
    unittest.main()
