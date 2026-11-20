import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "localmodel"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from localmodel import escalation_gate
from localmodel.escalation_gate import run
from tests.support.tmpdirs import tracked_tmpdir

FAILURE = "VERDICT: BLOCKED\nBLOCKED.md in the stage dir says task-1 is blocked.\n"


class SecurityWordsTest(unittest.TestCase):
    def test_whole_words_only(self):
        for ok in ("tests still fail after two tries, cannot execute the target", "evaluate the filepath and paths"):
            self.assertFalse(escalation_gate.security_escalation(f"ESCALATE: {ok}"), ok)
        self.assertTrue(escalation_gate.security_escalation("ESCALATE: shell exec with a variable"))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        os.makedirs(os.path.join(self.d, ".work", "s"))
        with open(os.path.join(self.d, ".work", "s", "task-1.md"), "w", encoding="utf-8") as f:
            f.write("Goal: demo\nPredicted paths: README.md\nTest: node --test t.js\n")
        self.fail_file = os.path.join(self.d, "f.md")
        with open(self.fail_file, "w", encoding="utf-8") as f:
            f.write(FAILURE)

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, answer):
        return run("s", "task-1", self.d, self.fail_file, llm=lambda p: json.dumps(answer) if isinstance(answer, dict) else answer)

    def log(self):
        with open(os.path.join(self.d, ".work", "s", "gate.jsonl"), encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def test_fail_with_a_real_quote_is_a_veto_and_logged(self):
        v, text = self.go({"verdict": "FAIL", "reason": "stale file", "evidence": "BLOCKED.md in the stage dir says task-1 is blocked."})
        self.assertEqual(v, "FAIL")
        self.assertIn("stale file", text)
        row = self.log()[0]
        self.assertTrue(row["veto"])
        self.assertEqual(row["judge"], "FAIL")

    def test_fail_with_an_invented_quote_is_not_a_veto(self):
        v, text = self.go({"verdict": "FAIL", "reason": "x", "evidence": "a line that is not in the report"})
        self.assertEqual(v, "DONE")
        self.assertIn("not a veto", text)
        self.assertFalse(self.log()[0]["veto"])

    def test_several_failure_files_reach_the_judge_oldest_first_newest_last(self):
        second = os.path.join(self.d, "f2.md")
        with open(second, "w", encoding="utf-8") as f:
            f.write("VERDICT: FAIL\n`node --test t.js` exited 3:\nSKIPPED/EMPTY: zero tests detected\n")
        seen = []
        run("s", "task-1", self.d, [self.fail_file, second], llm=lambda p: seen.append(p) or '{"verdict":"PASS","reason":"x","evidence":""}')
        p = seen[0]
        self.assertIn("earlier attempt 1 (f.md)", p)
        self.assertIn("--- f2.md ---", p)
        self.assertLess(p.index("BLOCKED.md in the stage"), p.index("zero tests detected"))
        self.assertEqual(self.log()[0]["failures"], [self.fail_file, second])

    def test_real_model_path_parses_prose_around_the_json(self):
        reply = 'Here is my answer: {"verdict": "FAIL", "reason": "env", "evidence": "x"} done'
        with mock.patch("search.strata.strata_up", return_value=True), mock.patch("localmodel.askmodel.llm", return_value=reply) as llm:
            self.assertEqual(escalation_gate.judge("p")["verdict"], "FAIL")
        self.assertEqual(llm.call_args.args[0], "p")
        self.assertTrue(llm.call_args.kwargs["think"])

    def test_real_model_path_fails_open(self):
        with mock.patch("search.strata.strata_up", return_value=False), mock.patch("localmodel.askmodel.llm") as llm:
            self.assertEqual(escalation_gate.judge("p"), {})
        llm.assert_not_called()  # Strata down: the model is not called
        with mock.patch("search.strata.strata_up", return_value=True), mock.patch("localmodel.askmodel.llm", side_effect=OSError("down")):
            self.assertEqual(escalation_gate.judge("p"), {})
        with mock.patch("search.strata.strata_up", return_value=True), mock.patch("localmodel.askmodel.llm", return_value="no json"):
            self.assertEqual(escalation_gate.judge("p"), {})

    def test_pass_goes_on(self):
        self.assertEqual(self.go({"verdict": "PASS", "reason": "code defect", "evidence": ""})[0], "DONE")

    def test_no_answer_fails_open(self):
        v, text = self.go("not json at all")
        self.assertEqual(v, "DONE")
        self.assertIn("skipped", text)
        self.assertEqual(self.log()[0]["judge"], "NONE")

    def test_security_risk_escalation_is_never_vetoed(self):
        # task-2 of the course-a-furigana stage: the judge vetoed this valid security ESCALATE as "not a file defect"
        with open(self.fail_file, "w", encoding="utf-8") as f:
            f.write("VERDICT: ESCALATE\nESCALATE: subprocess exec of a course.json path built from input, scripts/build.py:42\n")
        seen = []

        def veto(p):
            seen.append(p)
            return '{"verdict":"FAIL","reason":"not a file defect","evidence":"ESCALATE: subprocess exec of a course.json path built from input, scripts/build.py:42"}'

        v, text = run("s", "task-1", self.d, self.fail_file, llm=veto)
        self.assertEqual(v, "DONE")
        self.assertEqual(seen, [])  # the model is not asked at all
        self.assertFalse(self.log()[0]["veto"])
        self.assertIn("security", text)

    def test_other_escalation_still_goes_to_the_judge(self):
        with open(self.fail_file, "w", encoding="utf-8") as f:
            f.write("VERDICT: ESCALATE\nESCALATE: the refactor needs a new helper in three modules\n")
        v, _ = self.go({"verdict": "FAIL", "reason": "stale", "evidence": "ESCALATE: the refactor needs a new helper in three modules"})
        self.assertEqual(v, "FAIL")


if __name__ == "__main__":
    unittest.main()
