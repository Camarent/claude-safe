"""The model client split out of ask_code: llm request, query translation, the quote-verified judge and the reachability check."""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import askmodel
from search import strata
from tests.support.tmpdirs import tracked_mkdtemp


class LlmTests(unittest.TestCase):
    def call(self, **kw):
        reply = json.dumps({"content": [{"type": "text", "text": "a"}, {"type": "thinking", "text": "x"}, {"type": "text", "text": "b"}]}).encode()
        seen = {}

        def fake_open(req, timeout):
            seen["req"], seen["timeout"] = req, timeout
            return io.BytesIO(reply)

        with (
            mock.patch.object(askmodel, "wait_strata"),
            mock.patch.object(strata, "strata_url", return_value="http://s"),
            mock.patch.object(strata, "strata_key", return_value="k"),
            mock.patch.object(strata.urllib.request, "urlopen", side_effect=fake_open),
        ):
            out = askmodel.llm("hi", 50, **kw)
        return out, seen

    def test_joins_text_blocks_and_disables_thinking(self):
        out, seen = self.call()
        body = json.loads(seen["req"].data)
        self.assertEqual(out, "ab")
        self.assertEqual(seen["req"].full_url, "http://s/v1/messages")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["max_tokens"], 50)
        self.assertEqual(seen["req"].get_header("Authorization"), "Bearer k")

    def test_think_keeps_reasoning_on(self):
        _, seen = self.call(think=True)
        self.assertNotIn("thinking", json.loads(seen["req"].data))


class TranslateJudgeTests(unittest.TestCase):
    def test_translate_caps_and_hints(self):
        reply = json.dumps({"symbols": [str(i) for i in range(9)], "keywords": [str(i) for i in range(12)]})
        with mock.patch.object(askmodel, "llm", return_value=reply) as m:
            sym, kw = askmodel.translate("q", (["alpha"], ["beta"]), ["seen_fn"])
        self.assertEqual((len(sym), len(kw)), (6, 8))
        prompt = m.call_args.args[0]
        self.assertIn("alpha", prompt)
        self.assertIn("beta", prompt)
        self.assertIn("seen_fn", prompt)
        self.assertEqual(m.call_args.args[1], 700)

    def test_translate_think_budget(self):
        with mock.patch.object(askmodel, "THINK", {"translate"}), mock.patch.object(askmodel, "llm", return_value="{}") as m:
            self.assertEqual(askmodel.translate("q"), ([], []))
        self.assertEqual((m.call_args.args[1], m.call_args.kwargs["think"]), (3000, True))

    def test_judge_keeps_only_quoted_evidence(self):
        d = tracked_mkdtemp()
        Path(d, "a.py").write_text("def  cap():\n    return 3\n", encoding="utf-8")
        cands = [{"path": "a.py", "start": 1, "end": 2}, {"path": "a.py", "start": 2, "end": 2}]
        picks = [
            {"n": 0, "evidence": "def cap():"},  # whitespace-insensitive match
            {"n": 1, "evidence": "not there"},
            {"n": 7, "evidence": "def cap():"},
            {"n": 0},
            "junk",
        ]
        with mock.patch.object(askmodel, "llm", return_value=json.dumps({"relevant": picks})):
            self.assertEqual(askmodel.judge(d, "q", cands), [0])

    def test_snippet_unreadable_is_empty(self):
        self.assertEqual(askmodel.snippet("/nonexistent-root", {"path": "x", "start": 1, "end": 2}), "")


class UseModelTests(unittest.TestCase):
    def run_use(self, a, up, waited=False, **env):
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(askmodel, "strata_up", return_value=up),
            mock.patch.object(askmodel, "wait_strata", return_value=waited) as w,
            mock.patch.object(askmodel.os.path, "exists", return_value=False),
            contextlib.redirect_stderr(err),
        ):
            return askmodel.use_model(a), err.getvalue(), w

    def test_no_model_flag(self):
        self.assertFalse(self.run_use(["--no-model"], True)[0])

    def test_up(self):
        self.assertTrue(self.run_use([], True)[0])

    def test_down_notice(self):
        ok, err, w = self.run_use([], False, STRATA_URL="")
        self.assertFalse(ok)
        self.assertIn("no local model reachable", err)
        w.assert_not_called()

    def test_expected_server_waits(self):
        ok, _, w = self.run_use([], False, waited=True, STRATA_URL="http://x", STRATA_WAIT_TRIES="3")
        self.assertTrue(ok)
        w.assert_called_once_with(3)


if __name__ == "__main__":
    unittest.main()
