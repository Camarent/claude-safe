"""escalation_gate input and verdict rules: brief, plan notes and rules in the prompt, only the newest report decides, kinds, budgets, replay_gate."""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import escalation_gate, replay_gate
from pipeline.configuration import GATE_EARLIER_CHARS, GATE_NEWEST_CHARS, GATE_RULES_CHARS, LOCAL_CONTEXT_TOKENS
from pipeline.steps import _gate_files, _gate_veto
from pipeline.util import est_tokens
from tests.support.tmpdirs import tracked_tmpdir

BRIEF = "Goal: reword colliding prompts\nPredicted paths: a.json\nTest: node --test t.js\nBODY-LINE the brief body only here\n"
OLD = "VERDICT: ESCALATE\nESCALATE: Lead decision needed: source for radical glosses\n"
NEW = "VERDICT: FAIL\nreviewer: regex R2 wrong, fix the pattern\n"
MISSING = "VERDICT: ESCALATE\nESCALATE: no per-word reading source exists for 546 items\n"


def answer(**j):
    return lambda p: json.dumps(j)


class GateInputTest(unittest.TestCase):
    def setUp(self):
        tmp = tracked_tmpdir()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        Path(self.d, ".work", "s").mkdir(parents=True)
        Path(self.d, ".work", "s", "task-1.md").write_text(BRIEF, encoding="utf-8")
        self.old, self.new, self.notes, self.rules = (os.path.join(self.d, n) for n in ("old.md", "new.md", "notes.md", "AGENTS.md"))
        Path(self.old).write_text(OLD, encoding="utf-8")
        Path(self.new).write_text(NEW, encoding="utf-8")
        Path(self.notes).write_text("NOTES-LINE decisions of the plan\n", encoding="utf-8")
        Path(self.rules).write_text("RULES-LINE 8a: reword one side per colliding pair\n", encoding="utf-8")

    def go(self, llm, files=None, **kw):
        return escalation_gate.run("s", "task-1", self.d, files or [self.new], llm=llm, **kw)

    def test_prompt_has_every_section_and_pass_goes_on(self):
        seen = []

        def llm(p):
            seen.append(p)
            return '{"verdict":"PASS","reason":"rule 8a settles it","evidence":""}'

        v, _ = self.go(llm, [self.old, self.new], notes_file=self.notes, rules_file=self.rules)
        self.assertEqual(v, "DONE")
        p = seen[0]
        for part in ("BODY-LINE", "NOTES-LINE", "RULES-LINE 8a", "EARLIER ATTEMPTS", "NEWEST FAILURE REPORT"):
            self.assertIn(part, p)

    def test_stale_older_report_cannot_veto_but_stays_visible(self):
        seen = []

        def llm(p):
            seen.append(p)
            return json.dumps({"verdict": "FAIL", "reason": "r", "evidence": "ESCALATE: Lead decision needed: source for radical glosses"})

        v, text = self.go(llm, [self.old, self.new])
        self.assertEqual(v, "DONE")
        self.assertIn("not a veto", text)
        p = seen[0]
        self.assertGreater(p.index("NEWEST FAILURE REPORT"), p.index("Lead decision needed"))  # the old line sits in the earlier section
        self.assertIn("EARLIER ATTEMPTS", p[: p.index("NEWEST FAILURE REPORT")])
        self.assertEqual(p.count("regex R2 wrong"), 1)

    def test_only_the_newest_is_the_live_failure(self):
        earlier, newest = escalation_gate.read_failures([self.old, self.new])
        self.assertIn("Lead decision needed", earlier)
        self.assertNotIn("Lead decision needed", newest)
        self.assertIn("regex R2 wrong", newest)

    def test_missing_fact_quote_from_newest_is_a_veto_with_kind(self):
        Path(self.new).write_text(MISSING, encoding="utf-8")
        v, text = self.go(answer(verdict="FAIL", reason="no source", kind="missing_fact", evidence="ESCALATE: no per-word reading source exists for 546 items"))
        self.assertEqual(v, "FAIL")
        self.assertIn("[missing_fact]", text)
        row = [json.loads(x) for x in Path(self.d, ".work", "s", "gate.jsonl").read_text(encoding="utf-8").splitlines()][0]
        self.assertEqual((row["veto"], row["kind"]), (True, "missing_fact"))

    def test_quote_from_the_brief_is_not_a_veto(self):
        v, text = self.go(answer(verdict="FAIL", reason="r", evidence="BODY-LINE the brief body only here"))
        self.assertEqual(v, "DONE")
        self.assertIn("not a veto", text)

    def test_unknown_kind_is_ignored(self):
        v, text = self.go(answer(verdict="FAIL", reason="r", kind="weird", evidence="reviewer: regex R2 wrong, fix the pattern"))
        self.assertEqual(v, "FAIL")
        self.assertNotIn("[", text)

    def test_plan_error_kind_reaches_the_ask_message(self):
        v, text = self.go(
            answer(
                verdict="FAIL", reason="plan error: brief breaks the topic invariant", kind="plan_error", evidence="reviewer: regex R2 wrong, fix the pattern"
            )
        )
        self.assertEqual(v, "FAIL")
        gate = os.path.join(self.d, "g.md")
        Path(gate).write_text(f"VERDICT: FAIL\n{text}\n", encoding="utf-8")
        a = type("A", (), {"dir": self.d, "stage": "s"})()
        msg = _gate_veto(a, {"id": "task-1"}, {"file": "f.md"}, {"file": gate})["message"]
        self.assertIn("task-1: plan error: brief breaks the topic invariant | reviewer: regex R2 wrong, fix the pattern; the brief must change", msg)
        Path(gate).write_text("VERDICT: FAIL\njudge FAIL [missing_fact]: no source | q\n", encoding="utf-8")
        self.assertIn("(missing_fact: no source | q)", _gate_veto(a, {"id": "task-1"}, {"file": "f.md"}, {"file": gate})["message"])

    def test_gate_files_find_feature_notes_and_repo_rules(self):
        Path(self.d, "pipeline.json").write_text("{}", encoding="utf-8")
        ph = Path(self.d, ".work", "f", "phases", "01-x")
        ph.mkdir(parents=True)
        a = type("A", (), {"dir": self.d, "stage": "f/phases/01-x"})()
        os.remove(self.rules)
        self.assertEqual(_gate_files(a), "")  # neither file exists: skipped
        Path(self.d, ".work", "f", "plan-notes.md").write_text("n", encoding="utf-8")
        Path(self.rules).write_text("r", encoding="utf-8")
        self.assertEqual(_gate_files(a), f" --notes {self.d}/.work/f/plan-notes.md --rules {self.d}/AGENTS.md")
        flat = type("A", (), {"dir": self.d, "stage": "s"})()  # a plain stage keeps its notes in its own dir
        self.assertEqual(_gate_files(flat), f" --rules {self.d}/AGENTS.md")

    def test_missing_notes_and_rules_files_are_skipped(self):
        seen = []
        v, _ = self.go(lambda p: seen.append(p) or '{"verdict":"PASS","reason":"x","evidence":""}', notes_file=os.path.join(self.d, "none.md"), rules_file="")
        self.assertEqual(v, "DONE")
        self.assertNotIn("PLAN NOTES", seen[0])
        self.assertNotIn("PROJECT RULES", seen[0])

    def test_budgets_cut_rules_and_earlier_never_the_newest(self):
        Path(self.rules).write_text("R" * (GATE_RULES_CHARS * 3), encoding="utf-8")
        Path(self.old).write_text("E" * (GATE_EARLIER_CHARS * 5), encoding="utf-8")
        Path(self.new).write_text("N" * GATE_NEWEST_CHARS, encoding="utf-8")
        prompt = escalation_gate.gate_input(os.path.join(self.d, ".work", "s", "task-1.md"), [self.old, self.new], self.notes, self.rules)[0]
        self.assertNotIn("R" * (GATE_RULES_CHARS + 1), prompt)
        self.assertNotIn("E" * (GATE_EARLIER_CHARS + 1), prompt)
        self.assertIn("N" * GATE_NEWEST_CHARS, prompt)
        self.assertLess(est_tokens(prompt), LOCAL_CONTEXT_TOKENS // 2)

    def test_replay_runs_the_shared_builder_with_a_fake_llm(self):
        cases = os.path.join(self.d, "cases.json")
        case = {
            "name": "c1",
            "task_file": os.path.join(self.d, ".work", "s", "task-1.md"),
            "failure_files": [self.old, self.new],
            "notes_file": self.notes,
            "rules_file": self.rules,
            "expected": "PASS",
        }
        Path(cases).write_text(json.dumps([case]), encoding="utf-8")
        seen, out = [], io.StringIO()

        def llm(p):
            seen.append(p)
            return '{"verdict":"PASS","reason":"ok","evidence":""}'

        self.assertEqual(replay_gate.main([cases], llm=llm, out=out), 0)
        self.assertIn("RULES-LINE", seen[0])
        self.assertIn("ok  c1: expected PASS got PASS", out.getvalue())
        self.assertIn("summary: 1/1", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.d, ".work", "s", "gate.jsonl")))  # replay never writes the log
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(replay_gate.main([], out=io.StringIO()), 0)


if __name__ == "__main__":
    unittest.main()
