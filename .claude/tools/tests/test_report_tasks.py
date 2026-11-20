"""`Type: report` tasks: header parse, step sequence, `Inputs:` check, validator rule, pre-review file mode."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import prereview
from pipeline import plan_check, plan_files, plan_report, steps
from tests.support.project import dry_run, make_project
from tests.support.tmpdirs import tracked_mkdtemp

TABLE2 = (
    "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n| task-2 | b | dev | 1 | wave 1, haiku |\n\n"
    "## Graph\nwave 1: task-1, task-2\n"
)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def two_task_project(config=None):
    """-> project dir: task-1 is a code task, task-2 a report task."""
    d = make_project("no-retro no-audit", config=config)
    sd = os.path.join(d, ".work", "s")
    _write(os.path.join(sd, "TASKS.md"), TABLE2 + "flags: no-retro no-audit\n")
    _write(os.path.join(sd, "task-1.md"), "# task-1: code\nUI: no\nWave: W1\n\nPredicted paths: a.py\nTest: true\n")
    _write(os.path.join(sd, "task-2.md"), "# task-2: report\nType: report\nUI: no\nWave: W1\n\nPredicted paths: out.csv\nTest: python3 -I check.py\n")
    return d


class ParseTest(unittest.TestCase):
    def test_type_default_and_value(self):
        self.assertEqual(plan_files.task_type("# t\nUI: no\n"), "code")
        self.assertEqual(plan_files.task_type("# t\nType: Report\nUI: no\n"), "report")
        self.assertEqual(plan_files.task_type("# t\nType: weird\n"), "weird")

    def test_inputs_lines_and_commas(self):
        txt = "Inputs: a/b.txt, `tool:jq`\nInputs: after:task-1:out.csv\n"
        self.assertEqual(plan_files.task_inputs(txt), ["a/b.txt", "tool:jq", "after:task-1:out.csv"])
        self.assertEqual(plan_files.task_inputs("# t\n"), [])


class SequenceTest(unittest.TestCase):
    def test_report_task_has_prereview_no_quality_and_sonnet_reviewer(self):
        d = two_task_project(config={"prereview": True})
        rc, got, _ = dry_run(d)
        self.assertEqual(rc, 0)
        seq = [
            (role, model, "prereview" if "prereview.py" in rest else "quality" if "qualityrun.py" in rest else "test" if "testrun.py" in rest else "")
            for _, role, model, rest in got
        ]
        scripts = [x[2] for x in seq if x[2]]
        self.assertEqual(scripts.count("prereview"), 2, got)  # both tasks: the report task in file mode
        self.assertEqual(scripts.count("quality"), 1, got)  # the code task only
        self.assertEqual(scripts.count("test"), 2, got)  # both run their Test lines
        # task-2 (report): test run, pre-review (file mode), no quality run, then its reviewer on Sonnet; task-1 (code): pre-review right before its Haiku reviewer
        self.assertEqual([x[:2] for x in seq if x[0] == "reviewer"], [("reviewer", "sonnet"), ("reviewer", "haiku")], got)
        pre = [n for n, x in enumerate(seq) if x[2] == "prereview"]
        revs = [n for n, x in enumerate(seq) if x[0] == "reviewer"]
        self.assertEqual(len(pre), 2, got)
        self.assertLess(max(pre), min(revs), got)  # both pre-reviews run before either paid review
        self.assertEqual([("--task task-2" in got[n][3]) for n in pre], [True, False], got)
        self.assertNotIn("--task task-2", " ".join(x[3] for x in got if "qualityrun" in x[3]))

    def test_is_report_reads_the_task_file(self):
        d = two_task_project()
        a = mock.Mock(dir=d, stage="s")
        self.assertTrue(steps.is_report(a, {"id": "task-2"}))
        self.assertFalse(steps.is_report(a, {"id": "task-1"}))
        self.assertFalse(steps.is_report(a, {"id": "task-9"}))  # no file: code

    def test_reviewer_reason_text(self):
        d = two_task_project()
        a = mock.Mock(dir=d, stage="s")
        s = mock.Mock(reviewer_sonnet=False)
        with mock.patch.object(steps, "_disp", return_value={}):
            r = steps._disp_reviewer_model(a, {"id": "task-2"}, "x", s)
        self.assertIn("report task: content review on Sonnet", r["why"])


class InputsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="report-inputs-")
        self.ws = tracked_mkdtemp(prefix="report-ws-")
        _write(os.path.join(self.ws, "dict.txt"), "x")

    def check(self, line):
        _write(os.path.join(self.d, "task-1.md"), f"# t\nInputs: {line}\n")
        return plan_report.check_type_inputs(self.d, "task-1", self.ws, self.ws)

    def test_present_relative_and_absolute_pass(self):
        self.assertEqual(self.check("dict.txt"), [])
        self.assertEqual(self.check(os.path.join(self.ws, "dict.txt")), [])

    def test_missing_input_names_task_and_input(self):
        got = self.check("dict.txt, nope.txt")
        self.assertEqual(len(got), 1)
        self.assertIn("task-1", got[0])
        self.assertIn("nope.txt", got[0])

    def test_tool_found_or_missing(self):
        self.assertEqual(self.check("tool:sh"), [])
        self.assertIn("tool:no-such-tool-xyz", self.check("tool:no-such-tool-xyz")[0])

    def test_after_is_not_checked_and_no_inputs_is_unchanged(self):
        self.assertEqual(self.check("after:task-3:out/none.csv"), [])
        _write(os.path.join(self.d, "task-1.md"), "# t\nPredicted paths: a\n")
        self.assertEqual(plan_report.check_type_inputs(self.d, "task-1", self.ws, self.ws), [])

    def test_unknown_type_is_an_error(self):
        _write(os.path.join(self.d, "task-1.md"), "# t\nType: weird\n")
        self.assertIn("Type", plan_report.check_type_inputs(self.d, "task-1", self.ws, self.ws)[0])

    def test_wired_into_the_task_file_check(self):
        self.assertIn(plan_check.check_type_inputs, vars(plan_check).values())
        self.assertIn(plan_check.check_report_validator, vars(plan_check).values())


class ValidatorRuleTest(unittest.TestCase):
    def check(self, head, test):
        d = tracked_mkdtemp(prefix="report-val-")
        _write(os.path.join(d, "task-1.md"), f"# t\n{head}Test: {test}\n")
        return plan_report.check_report_validator(d, "task-1")

    def test_existence_only_fails_for_report(self):
        for t in ("test -s out.csv", "test -f out.csv", "[ -s out.csv ]", "ls out.csv", "test -s a && test -f b"):
            got = self.check("Type: report\n", t)
            self.assertEqual(len(got), 1, t)
            self.assertIn("needs a validator script", got[0])

    def test_validator_passes(self):
        self.assertEqual(self.check("Type: report\n", "python3 -I validate.py out.csv"), [])
        self.assertEqual(self.check("Type: report\n", "test -s out.csv && python3 -I validate.py"), [])

    def test_code_task_unchanged(self):
        self.assertEqual(self.check("", "test -s out.csv"), [])


class SafetyNetTest(unittest.TestCase):
    def test_all_outside(self):
        d = tracked_mkdtemp(prefix="report-repo-")
        out = tracked_mkdtemp(prefix="report-out-")
        self.assertTrue(prereview._all_outside_repo([os.path.join(out, "r.md")], d))
        self.assertTrue(prereview._all_outside_repo([os.path.join(d, ".work", "f", "r.md")], d))
        self.assertFalse(prereview._all_outside_repo(["a.py"], d))
        self.assertFalse(prereview._all_outside_repo([os.path.join(out, "r.md"), "a.py"], d))
        self.assertFalse(prereview._all_outside_repo([], d))


class FileModeTest(unittest.TestCase):
    """prereview file mode: the deliverable files are judged in chunks; the model is a fake (`review`)."""

    def project(self, files, paths=None, typ="Type: report\n"):
        d = tracked_mkdtemp(prefix="report-repo-")
        os.makedirs(os.path.join(d, ".work", "s"))
        for name, text in files.items():
            _write(os.path.join(d, name), text)
        paths = paths if paths is not None else list(files)
        _write(os.path.join(d, ".work", "s", "task-1.md"), f"# t\n{typ}Predicted paths: {', '.join(paths)}\nDone: C1 every row has a name\n")
        return d

    def run_fake(self, d, replies):
        calls = []

        def review(repo, *args, t=None):
            calls.append(args)
            return replies(len(calls), open(args[args.index("--files") + 1], encoding="utf-8").read())

        git = mock.Mock(side_effect=AssertionError("no git in file mode"))
        return (*prereview.run("s", "task-1", d, git=git, review=review), calls)

    def test_picks_only_deliverable_text_files(self):
        d = self.project({"r.md": "a\n", "check.py": "x\n", "bin.csv": "x\n"}, paths=["r.md", "check.py", "bin.csv", "gone.md"])
        with open(os.path.join(d, "bin.csv"), "wb") as fh:
            fh.write(b"\xff\xfe\x00")
        v, _note, calls = self.run_fake(d, lambda n, part: (0, "PASS fine"))
        self.assertEqual((v, len(calls)), ("DONE", 1))

    def test_no_file_is_skipped_naming_paths(self):
        d = self.project({"check.py": "x\n"})
        v, note, calls = self.run_fake(d, lambda n, part: (0, "PASS"))
        self.assertEqual((v, calls), ("SKIPPED", []))
        self.assertIn("no deliverable file found", note)
        self.assertIn("check.py", note)

    def test_all_files_in_one_call_with_headers_and_line_numbers(self):
        d = self.project({"r.md": "ok row\nsecond\n", "s.md": "third\n"})
        seen = []
        v, _note, calls = self.run_fake(d, lambda n, body: seen.append(body) or (0, "PASS fine"))
        self.assertEqual((v, len(calls)), ("DONE", 1))
        self.assertIn("=== r.md ===\n1: ok row\n2: second\n", seen[0])
        self.assertIn("=== s.md ===\n1: third\n", seen[0])
        self.assertIn("judgefiles", calls[0])

    def test_oversize_is_skipped_with_reason_and_no_call(self):
        d = self.project({"r.md": "".join(f"line {i} " + "x" * 30 + "\n" for i in range(12000))})  # ~500 KB
        v, note, calls = self.run_fake(d, lambda n, part: (0, "PASS"))
        self.assertEqual((v, calls), ("SKIPPED", []))
        self.assertRegex(note, r"input ~\d+ est\. tokens > budget \d+")

    def test_all_verified_findings_listed(self):
        d = self.project({"r.md": "ok row\nbad one\nbad two\n"})
        out = "FAIL  2\nno name  | r.md:2 bad one\nno name  | r.md:3 2: bad two\nghost  | r.md:7 nothing here"
        v, note, _ = self.run_fake(d, lambda n, part: (1, out))
        self.assertEqual(v, "FAIL")
        self.assertIn("bad one", note)
        self.assertIn("bad two", note)
        self.assertNotIn("nothing here", note)

    def test_verified_fail_quotes_the_line_and_stamps_sha(self):
        d = self.project({"r.md": "ok row\nbad row here\n"})
        v, note, _ = self.run_fake(d, lambda n, part: (1, "FAIL no name  | r.md:2 bad row here"))
        self.assertEqual(v, "FAIL")
        self.assertIn("bad row here", note)
        self.assertRegex(note, r"diff-sha: [0-9a-f]{16}")

    def test_fail_with_quote_not_in_chunk_is_unclear(self):
        d = self.project({"r.md": "ok row\n"})
        v, note, _ = self.run_fake(d, lambda n, part: (1, "FAIL  | r.md:9 invented line"))
        self.assertEqual(v, "DONE")
        self.assertIn("unclear", note)

    def test_server_down_fails_open(self):
        d = self.project({"r.md": "ok\n"})
        v, _, _ = self.run_fake(d, lambda n, part: (0, "skipped: local model down or off (judgefiles)"))
        self.assertEqual(v, "SKIPPED")

    def test_outside_paths_without_report_type_use_file_mode(self):
        out = tracked_mkdtemp(prefix="report-out-")
        d = self.project({}, paths=[os.path.join(out, "r.md")], typ="")
        _write(os.path.join(out, "r.md"), "a\n")
        v, _, calls = self.run_fake(d, lambda n, part: (0, "PASS ok"))
        self.assertEqual((v, len(calls)), ("DONE", 1))


if __name__ == "__main__":
    unittest.main()
