"""retro/ab_caveman.py with a fake `claude` on PATH (no network): plan calls nothing, arms differ only by the caveman block, budget stops the run, report table."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS / "retro"))
import ab_caveman as ab  # noqa: E402  (path set up above)

BLOCK = "<!-- caveman:begin -->\nStyle: terse.\n\n## Rules\nDrop filler.\n<!-- caveman:end -->"
AGENT = "---\nname: {name}\ndescription: test {name}\ntools: Read\nmodel: haiku\n---\n\nYou review things.\n\n{block}\n\nTail line.\n"
FAKE = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\\n")
agents = json.loads(argv[argv.index("--agents") + 1])
name = argv[argv.index("--agent") + 1]
terse = "caveman:begin" in agents[name]["prompt"]
text = "VERDICT: PASS\\nok" if name == "reviewer" else "word " * (5 if terse else 20)
print(json.dumps({"result": text, "num_turns": 2, "total_cost_usd": float(os.environ.get("FAKE_USD", "0.5")),
                  "usage": {"output_tokens": 5 if terse else 20}, "duration_ms": 1000, "session_id": "", "is_error": False}))
"""


class AbCavemanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.agents = self.root / "agents"
        self.agents.mkdir()
        for role in ab.ROLES:
            (self.agents / f"{role}.md").write_text(AGENT.format(name=role, block=BLOCK), encoding="utf-8")
        bindir = self.root / "bin"
        bindir.mkdir()
        fake = bindir / "claude"
        fake.write_text(FAKE, encoding="utf-8")
        fake.chmod(0o755)
        self.log = self.root / "calls.jsonl"
        env = mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(self.log), "FAKE_USD": "0.5"})
        env.start()
        self.addCleanup(env.stop)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]

    def test_strip_block_removes_exactly_one_block(self):
        body = ab.split_frontmatter(AGENT.format(name="x", block=BLOCK))[1]
        stripped = ab.strip_block(body)
        self.assertNotIn("caveman:", stripped)
        self.assertIn("Tail line.", stripped)
        with self.assertRaises(ValueError):
            ab.strip_block(stripped)
        with self.assertRaises(ValueError):
            ab.strip_block(body + "\n" + BLOCK)

    def test_arms_differ_only_by_block(self):
        a_desc, a = ab.arm_prompt(str(self.agents), "reviewer", "A")
        b_desc, b = ab.arm_prompt(str(self.agents), "reviewer", "B")
        self.assertEqual(a_desc, b_desc)
        self.assertIn("caveman:begin", a)
        self.assertNotIn("caveman:", b)
        self.assertEqual(b, ab.strip_block(a).strip())  # arm B is exactly arm A minus the block

    def test_plan_is_interleaved_and_paired(self):
        runs = ab.plan_runs(["reviewer", "scout"], {}, {})
        self.assertEqual(len(runs), 2 * 3 * 2)
        pairs: dict = {}
        for r in runs:
            pairs.setdefault(r["pair"], []).append(r["arm"])
        self.assertTrue(all(sorted(v) == ["A", "B"] for v in pairs.values()))
        self.assertEqual(runs[0]["arm"], "A")  # reviewer r1 starts on A
        self.assertEqual([r["arm"] for r in runs if r["role"] == "scout"][0], "B")  # scout starts on B: order alternates per role

    def test_plan_prints_and_calls_nothing(self):
        out = subprocess.run(
            [sys.executable, "-I", str(TOOLS / "retro" / "ab_caveman.py"), "plan", "--budget", "8", "--role", "reviewer"],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "ORCH_AGENTS_DIR": str(self.agents)},
        )
        self.assertIn("estimated usd", out.stdout)
        self.assertIn("nothing was called", out.stdout)
        self.assertEqual(self.calls(), [])

    def test_run_stops_at_budget_and_keeps_partial_results(self):
        out = self.root / "out"
        runs = ab.plan_runs(["scout"], {}, {})
        spent = ab.cmd_run(runs, 1.2, str(out), str(self.agents))
        rows = ab.read_jsonl(str(out / "results.jsonl"), dicts_only=True)
        self.assertEqual(len(rows), 3)  # 0.5, 1.0 (< 1.2), 1.5 (>= 1.2) then stop
        self.assertAlmostEqual(spent, 1.5)
        self.assertEqual(len(self.calls()), 3)

    def test_run_passes_remaining_budget_and_cleans_cwd(self):
        out = self.root / "out"
        ab.cmd_run(ab.plan_runs(["scout"], {}, {})[:2], 10.0, str(out), str(self.agents))
        calls = self.calls()
        budget_flags = [c["argv"][c["argv"].index("--max-budget-usd") + 1] for c in calls]
        self.assertEqual(budget_flags[0], "10.0000")
        self.assertEqual(budget_flags[1], "9.5000")
        for c in calls:
            self.assertFalse(os.path.exists(c["cwd"]))  # temp cwd removed after the run
            self.assertIn("--agents", c["argv"])

    def test_run_refuses_existing_results(self):
        out = self.root / "out"
        out.mkdir()
        (out / "results.jsonl").write_text("", encoding="utf-8")
        with self.assertRaises(SystemExit):
            ab.cmd_run(ab.plan_runs(["scout"], {}, {}), 1.0, str(out), str(self.agents))
        self.assertEqual(self.calls(), [])

    def test_record_and_report(self):
        out = self.root / "out"
        ab.cmd_run(ab.plan_runs(["reviewer"], {}, {}), 100.0, str(out), str(self.agents))
        rows = ab.read_jsonl(str(out / "results.jsonl"), dicts_only=True)
        self.assertTrue(all(r["ev"] == "session" for r in rows))
        # fake claude: block present (arm A) -> 5 output tokens; block stripped (arm B) -> 20
        self.assertTrue(all(r["output"] == 5 for r in rows if r["arm"] == "A"))
        self.assertTrue(all(r["output"] == 20 for r in rows if r["arm"] == "B"))
        self.assertEqual(ab.paired_diffs(rows)["reviewer"][0], -15)
        text = ab.report_text(rows)
        self.assertIn("reviewer", text)
        self.assertIn("paired", text)

    def test_verdict_agreement_counts_fixture_expectation(self):
        rows = [
            {"ev": "session", "role": "reviewer", "arm": "A", "pair": "p", "output": 1, "words": 1, "usd": 0, "turns": 1, "agree": True},
            {"ev": "session", "role": "reviewer", "arm": "A", "pair": "q", "output": 1, "words": 1, "usd": 0, "turns": 1, "agree": False},
        ]
        self.assertEqual(ab.summarise(rows)[("reviewer", "A")]["agree"], "1/2")


if __name__ == "__main__":
    unittest.main()
