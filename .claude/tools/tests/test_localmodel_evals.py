"""find_eval (offline --no-model path, lp stubbed) and context_eval main (plan_context and Strata stubbed). No network, no model."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import context_eval as ce
from localmodel import find_eval as fe
from search import strata
from tests.support.tmpdirs import tracked_mkdtemp


def run(mod, *argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main(list(argv))
    return rc, buf.getvalue()


class FindEval(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.calls: list = []
        answers = {
            "where is cart": "src/cart.py:1-9  keyword\nsrc/other.py:3-4  keyword\n",
            "who logs in": "src/other.py:1-2  keyword\nsrc/b.py:1-2  keyword\nsrc/user.py:1-2  keyword\n",
            "nothing known": "",
        }

        def fake_lp(repo, *a, t=60):
            self.calls.append(a)
            return answers[a[1]]

        mock.patch.object(fe, "lp", fake_lp).start()
        self.addCleanup(mock.patch.stopall)
        self.q = os.path.join(self.d, "q.json")
        Path(self.q).write_text(
            json.dumps(
                [
                    {"q": "where is cart", "expect": ["src/cart.py"]},
                    {"q": "who logs in", "expect": ["src/user.py"]},
                    {"q": "nothing known", "expect": ["src/x.py"]},
                ]
            ),
            encoding="utf-8",
        )
        self.base = os.path.join(self.d, "base.json")

    def test_ask_offline_passes_no_model_and_dedups(self):
        mock.patch.object(fe, "lp", lambda repo, *a, t=60: self.calls.append(a) or "a.py:1-2 k\na.py:3-4 k\nb.py:1 k\n").start()
        self.assertEqual(fe.ask(self.d, "q", False), ["a.py", "b.py"])
        self.assertEqual(self.calls[-1], ("find", "q", "--no-model"))
        fe.ask(self.d, "q", True)
        self.assertEqual(self.calls[-1], ("find", "q"))

    def test_evaluate_counts_hits_idk_and_misses(self):
        res, misses = fe.evaluate(self.d, json.loads(Path(self.q).read_text(encoding="utf-8")))
        self.assertEqual(res, {"n": 3, "hit1": 0.333, "hit3": 0.667, "idk": 0.333})
        self.assertEqual(misses, [("nothing known", [])])
        self.assertEqual(fe.evaluate(self.d, [])[0], {"n": 0, "hit1": 0, "hit3": 0, "idk": 0})

    def test_main_write_baseline_then_pass_then_regress(self):
        rc, out = run(fe, "--repo", self.d, "--questions", self.q, "--baseline", self.base, "--write-baseline")
        self.assertEqual(rc, 0)
        self.assertIn('"hit3": 0.667', out)
        self.assertIn("MISS: nothing known -> (nothing)", out)
        self.assertEqual(json.loads(Path(self.base).read_text(encoding="utf-8"))["hit3"], 0.667)
        self.assertEqual(run(fe, "--repo", self.d, "--questions", self.q, "--baseline", self.base)[0], 0)
        Path(self.base).write_text('{"hit3": 0.9}', encoding="utf-8")
        rc, out = run(fe, "--repo", self.d, "--questions", self.q, "--baseline", self.base)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL: hit@3 0.667 < baseline 0.9", out)

    def test_main_without_baseline(self):
        rc, out = run(fe, "--repo", self.d, "--questions", self.q, "--baseline", os.path.join(self.d, "none.json"))
        self.assertEqual(rc, 0)
        self.assertTrue(out.endswith("no baseline\n"))


class ContextEvalMain(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        Path(self.d, "prompt.md").write_text("the goal text\n", encoding="utf-8")
        self.gold = os.path.join(self.d, "gold.json")
        Path(self.gold).write_text(json.dumps({"repo": self.d, "prompt": "prompt.md", "files": ["a.py", "z.py"], "workspace": self.d}), encoding="utf-8")
        self.addCleanup(mock.patch.stopall)

    def stub(self, up=True):
        mock.patch.object(strata, "strata_up", lambda: up).start()

        def fake(root, text, cfg, use_cache=True, per=None):
            if per is not None:
                per.append(("line one two three four five", ["a"] if use_cache else ["a", "b"], use_cache))
            return ["a.py", "b.py"]

        mock.patch.object(ce.pc, "model_files", fake).start()

    def test_skipped_when_strata_down(self):
        self.stub(up=False)
        rc, out = run(ce, self.gold)
        self.assertEqual((rc, out), (3, "EVAL SKIPPED: local model (Strata) is down\n"))

    def test_cold_line_and_out_file(self):
        self.stub()
        out_file = os.path.join(self.d, "res.json")
        rc, out = run(ce, self.gold, "--runs", "2", "--out", out_file)
        self.assertEqual(rc, 0)
        self.assertRegex(
            out,
            r"^EVAL cold: recall 1/1 of 2 \(spread 1-1\), named 2, secs \d+/\d+\nEVAL meta: model \S+, prompt [0-9a-f]{10}, files listed \d+, date \d{4}-\d\d-\d\d\n$",
        )
        res = json.loads(Path(out_file).read_text(encoding="utf-8"))
        self.assertEqual(len(res["cold"]), 2)
        self.assertNotIn("cache", res)
        self.assertEqual(sorted(res["meta"]), ["date", "files_listed", "model", "prompt_hash"])

    def test_quick_uses_one_run_and_three_lines(self):
        seen = []
        self.stub()
        orig = ce.pc.model_files
        mock.patch.object(ce.pc, "model_files", lambda r, t, c, **k: seen.append(t) or orig(r, t, c, **k)).start()
        Path(self.d, "prompt.md").write_text("\n".join(f"line {i} has six words in it" for i in range(5)), encoding="utf-8")
        out_file = os.path.join(self.d, "q.json")
        rc, out = run(ce, self.gold, "--runs", "3", "--quick", "--out", out_file)
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("EVAL quick (not comparable to a full run)\n"))
        self.assertIn("request lines 3", out)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0].splitlines()), 3)
        self.assertEqual(json.loads(Path(out_file).read_text(encoding="utf-8"))["quick_lines"], 3)

    def test_check_cache_line(self):
        self.stub()
        rc, out = run(ce, self.gold, "--runs", "1", "--check-cache")
        self.assertEqual(rc, 0)
        cache_line = out.splitlines()[2]
        self.assertTrue(
            cache_line.startswith("EVAL cache: 1 cached lines, overlap cached-vs-fresh mean 0.50 (min 0.50), fresh-vs-fresh mean 1.00 (min 1.00): ")
        )
        self.assertTrue(cache_line.endswith("LOWER than the model's own noise: check the cache key"))


if __name__ == "__main__":
    unittest.main()
