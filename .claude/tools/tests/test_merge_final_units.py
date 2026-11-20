import contextlib
import io
import json
import os
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import merge_wave
import wt
from audit import final_check
from tests.support.tmpdirs import tracked_mkdtemp


def write_cfg(d, cfg):
    with open(os.path.join(d, "pipeline.json"), "w") as f:
        json.dump(cfg, f)


class MergeWaveUnits(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

    def test_conflict_and_blocked_stop_early(self):
        write_cfg(self.d, {})
        with mock.patch.object(wt, "merge", side_effect=wt.Conflict("boom")):
            v, text = merge_wave.run(self.d, "s", ["t1"])
        self.assertEqual(v, "FAIL")
        self.assertIn("t1: merge conflict", text)
        with mock.patch.object(wt, "merge", side_effect=wt.Blocked("dirty")):
            v, text = merge_wave.run(self.d, "s", ["t1"])
        self.assertEqual((v, text.startswith("t1: dirty")), ("BLOCKED", True))

    def test_no_gates_notes_it_and_removes(self):
        write_cfg(self.d, {})
        with mock.patch.object(wt, "merge", return_value="merged"), mock.patch.object(wt, "remove") as rm:
            v, text = merge_wave.run(self.d, "s", ["t1"])
        self.assertEqual(v, "DONE")
        self.assertIn("no `gates`", text)
        self.assertIn("t1: removed", text)
        rm.assert_called_once()

    def test_red_check_fails_and_keeps_branches(self):
        write_cfg(self.d, {"gates": [{"name": "x", "cmd": "x"}]})
        red = [("x", 1, 0, "bad")]
        with (
            mock.patch.object(wt, "merge", return_value="m"),
            mock.patch.object(final_check, "run_all", return_value=red),
            mock.patch.object(wt, "remove") as rm,
        ):
            v, text = merge_wave.run(self.d, "s", ["t1"], runner=lambda *a, **k: None)
        self.assertEqual(v, "FAIL")
        self.assertIn("red after the merge", text)
        rm.assert_not_called()

    def test_remove_error_is_blocked(self):
        write_cfg(self.d, {})
        with mock.patch.object(wt, "merge", return_value="m"), mock.patch.object(wt, "remove", side_effect=RuntimeError("held")):
            v, text = merge_wave.run(self.d, "s", ["t1"])
        self.assertEqual(v, "BLOCKED")
        self.assertIn("t1: held", text)

    def test_main_prints_verdict(self):
        out = io.StringIO()
        with mock.patch.object(merge_wave, "run", return_value=("DONE", "ok")) as r, contextlib.redirect_stdout(out):
            self.assertEqual(merge_wave.main(["--dir", self.d, "--stage", "s", "--tasks", "a,,b"]), 0)
        self.assertEqual(out.getvalue(), "VERDICT: DONE\nok\n")
        self.assertEqual(r.call_args[0][2], ["a", "b"])


class FinalCheckUnits(unittest.TestCase):
    def test_main_without_commands(self):
        d = tracked_mkdtemp()
        write_cfg(d, {})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(final_check.main(["--dir", d]), 0)
        self.assertIn("VERDICT: FAIL", out.getvalue())
        self.assertIn("nothing to run", out.getvalue())

    def test_main_runs_in_repo_subfolder(self):
        d = tracked_mkdtemp()
        os.makedirs(os.path.join(d, "r"))
        write_cfg(d, {"repo": "r", "gates": [{"name": "a", "cmd": "true"}, {"name": "b", "cmd": "true"}]})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            final_check.main(["--dir", d])
        self.assertIn("VERDICT: DONE", out.getvalue())
        self.assertIn("2 of 2 commands passed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
