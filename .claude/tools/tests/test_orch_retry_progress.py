import contextlib
import io
import json
import os
import sys
import types
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import cli_orch  # noqa: E402  (path set up above)


class RetryProgressTest(unittest.TestCase):
    def test_progress_line_printed_before_refresh(self):
        events = []

        class Err(io.StringIO):
            def write(self, s):
                events.append(("stderr", s))
                return super().write(s)

        def fake_refresh(*args, **kw):
            events.append(("refresh", args))
            return {"rebased": [], "discarded": []}

        a = types.SimpleNamespace(dir="/d", stage="s", step="step-a")
        err, out = Err(), io.StringIO()
        with (
            unittest.mock.patch.object(cli_orch.worktree, "refresh", side_effect=fake_refresh),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(out),
        ):
            cli_orch._refresh_job(a, "unused.jsonl")
        expected = cli_orch.REFRESH_PROGRESS.format(step="step-a")
        self.assertIn(("stderr", expected), events)
        self.assertLess(events.index(("stderr", expected)), events.index(("refresh", ("/d", "s", "step-a"))))
        self.assertEqual(out.getvalue(), "")

    def test_stdout_json_unchanged_when_refreshed(self):
        a = types.SimpleNamespace(dir="/d", stage="s", step="step-a")
        res = {"rebased": ["main"], "discarded": []}
        err, out = io.StringIO(), io.StringIO()
        with (
            unittest.mock.patch.object(cli_orch.worktree, "refresh", return_value=res),
            unittest.mock.patch.object(cli_orch, "append_event"),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(out),
        ):
            cli_orch._refresh_job(a, "unused.jsonl")
        self.assertEqual(json.loads(out.getvalue()), {"refreshed": "step-a", **res})
        self.assertIn(cli_orch.REFRESH_PROGRESS.format(step="step-a"), err.getvalue())


if __name__ == "__main__":
    unittest.main()
