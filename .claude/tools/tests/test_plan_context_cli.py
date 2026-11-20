"""plan_context CLI: the `index` command and `stage --workspace`."""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import plan_context as pc
from tests.support.tmpdirs import tracked_mkdtemp


class Cli(unittest.TestCase):
    def test_index_prints_one_line_per_repo(self):
        out = io.StringIO()
        with mock.patch.object(pc, "ensure_index", return_value=[("/w/a", 3), ("/w/b", 5)]), contextlib.redirect_stdout(out):
            self.assertEqual(pc.main(["index", "--workspace", "/w"]), 0)
        self.assertEqual(out.getvalue().splitlines(), ["index /w/a: 3 s", "index /w/b: 5 s"])

    def test_stage_with_workspace_passes_the_workspace_config(self):
        d = tracked_mkdtemp()
        out = os.path.join(d, "o", "ctx.md")
        cfg = {"marker": 1}
        with (
            mock.patch.object(pc, "ctx_config", return_value=cfg) as cc,
            mock.patch.object(pc, "stage_context", return_value="a\nb\n") as sc,
            contextlib.redirect_stdout(io.StringIO()) as buf,
        ):
            rc = pc.main(["stage", d, "--stage-dir", d, "--base", "B", "--head", "H", "--out", out, "--workspace", d, "--verdict"])
        self.assertEqual(rc, 0)
        cc.assert_called_once_with(d)
        sc.assert_called_once_with(d, d, "B", "H", cfg)
        self.assertTrue(buf.getvalue().startswith("VERDICT: DONE\n"))
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "a\nb\n")

    def test_stage_without_workspace_uses_no_config(self):
        d = tracked_mkdtemp()
        with mock.patch.object(pc, "stage_context", return_value="x") as sc, contextlib.redirect_stdout(io.StringIO()):
            pc.main(["stage", d, "--stage-dir", d, "--base", "B", "--head", "H", "--out", os.path.join(d, "c.md")])
        self.assertIsNone(sc.call_args.args[4])


if __name__ == "__main__":
    unittest.main()
