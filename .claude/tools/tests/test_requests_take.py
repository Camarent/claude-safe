"""Characterization tests for requests.take_request: validation, budgets, gating and the dispatch."""

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import requests
from pipeline.util import read_jsonl
from tests.support.tmpdirs import tracked_mkdtemp

TABLE = "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n## Graph\nwave 1: task-1\n"


class TakeRequestTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="synthetic-project-")
        self.w = os.path.join(self.d, ".work", "s")
        os.makedirs(os.path.join(self.w, "requests"))
        with open(os.path.join(self.w, "TASKS.md"), "w", encoding="utf-8") as f:
            f.write(TABLE)
        self.log = os.path.join(self.w, "orch.jsonl")
        self.a = SimpleNamespace(dir=self.d, stage="s")
        env = mock.patch.dict(os.environ, {"ORCH_AGENTS_DIR": os.path.join(self.d, "no-agents"), "ORCH_GATED": "publisher", "ORCH_MAX_DISPATCH": "5"})
        env.start()
        self.addCleanup(env.stop)
        with open(os.path.join(self.w, "brief.md"), "w", encoding="utf-8") as f:
            f.write("do it")

    def put(self, name, req):
        with open(os.path.join(self.w, "requests", name), "w", encoding="utf-8") as f:
            f.write(req if isinstance(req, str) else json.dumps(req))

    def take(self):
        return requests.take_request(self.a, self.w, self.log)

    def rejects(self):
        return [(e["request"], e["why"]) for e in read_jsonl(self.log) if e["ev"] == "reject"]

    def test_no_requests_dir(self):
        os.rmdir(os.path.join(self.w, "requests"))
        self.assertIsNone(self.take())

    def test_valid_request_becomes_a_dispatch_and_moves_to_done(self):
        self.put("r1.json", {"agent": "scout", "model": "haiku", "brief": ".work/s/brief.md"})
        r = self.take()
        self.assertEqual(
            r,
            {
                "action": "dispatch",
                "id": "a1",
                "agent": "scout",
                "model": "haiku",
                "task": None,
                "role": "other",
                "why": "requested by file r1.json: .work/s/brief.md",
                "prompt": "Read .work/s/brief.md and do what it says. Write your full report to .work/s/reports/report-a1.md, then reply with exactly: REPORT a1",
            },
        )
        self.assertTrue(os.path.exists(os.path.join(self.w, "requests", "done", "r1.json")))
        self.assertFalse(os.path.exists(os.path.join(self.w, "requests", "r1.json")))

    def test_bad_requests_are_rejected_then_the_next_one_is_taken(self):
        self.put("a.json", "not json")
        self.put("b.json", {"agent": "Bad Name", "brief": ".work/s/brief.md"})
        self.put("c.json", {"agent": "scout", "model": "gpt", "brief": ".work/s/brief.md"})
        self.put("d.json", {"agent": "scout", "brief": "../outside.md"})
        self.put("e.json", {"agent": "scout", "brief": ".work/s/brief.md"})
        self.put("skip.txt", "ignored")
        self.assertEqual(self.take()["why"], "requested by file e.json: .work/s/brief.md")
        self.assertEqual(
            self.rejects(),
            [
                ("a.json", "unknown agent None"),
                ("b.json", "unknown agent 'Bad Name'"),
                ("c.json", "bad model 'gpt'"),
                ("d.json", "brief must be an existing file inside .work/<stage>/"),
            ],
        )

    def test_request_id_does_not_collide_with_haiku_dispatches(self):
        # 3 dispatches, 1 Sonnet: the id must follow all dispatches (a4), not the heavy count (a2 is taken)
        for i, model in enumerate(("haiku", "haiku", "sonnet"), 1):
            with open(self.log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ev": "dispatch", "id": f"a{i}", "agent": "scout", "model": model}) + "\n")
        self.put("r.json", {"agent": "scout", "model": "haiku", "brief": ".work/s/brief.md"})
        r = self.take()
        self.assertEqual(r["id"], "a4")
        self.assertIn("report-a4.md", r["prompt"])

    def test_non_object_or_non_string_brief_is_rejected_not_a_traceback(self):
        self.put("list.json", "[]")
        self.put("str.json", '"x"')
        self.put("num.json", {"agent": "scout", "brief": 5})
        self.assertIsNone(self.take())  # no crash
        self.assertEqual(
            self.rejects(),
            [
                ("list.json", "request must be a JSON object"),
                ("num.json", "brief must be an existing file inside .work/<stage>/"),
                ("str.json", "request must be a JSON object"),
            ],
        )
        self.assertTrue(os.path.exists(os.path.join(self.w, "requests", "list.json")))  # a bad request is not moved to done/
        self.assertFalse(os.path.exists(os.path.join(self.w, "requests", "done", "list.json")))
        self.assertIsNone(self.take())  # and is not rejected again on the next call
        self.assertEqual(len(self.rejects()), 3)

    def test_dispatch_budget_asks_main(self):
        self.put("r.json", {"agent": "scout", "brief": ".work/s/brief.md"})
        with mock.patch.dict(os.environ, {"ORCH_MAX_DISPATCH": "0"}):
            r = self.take()
        self.assertEqual(r, {"action": "ask_main", "message": "Sonnet/Opus dispatch budget reached (0 of 0); request r.json not run"})

    def test_opus_budget_rejects(self):
        self.put("r.json", {"agent": "scout", "model": "opus", "brief": ".work/s/brief.md"})
        with mock.patch.dict(os.environ, {"ORCH_MAX_OPUS": "0"}):
            self.assertIsNone(self.take())
        self.assertEqual(self.rejects(), [("r.json", "opus budget used up")])

    def test_gated_agent_asks_main(self):
        self.put("r.json", {"agent": "publisher", "brief": ".work/s/brief.md"})
        r = self.take()
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("lead asks to run 'publisher' (outward-facing)", r["message"])
        self.assertTrue(r["message"].endswith("brief: .work/s/brief.md"))


if __name__ == "__main__":
    unittest.main()
