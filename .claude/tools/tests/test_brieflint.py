import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brieflint
from pipeline import briefs, plan_check
from pipeline.configuration import AGENTS_DIR, TOOLS_DIR
from search import ctxpack
from tests.support.tmpdirs import tracked_mkdtemp

GOOD = "Files (only these): web/a.js\nLoop: python3 -I /root/.claude/tools/qrun.py -- node --test x\nDone: suite green. see web/a.js:24. turns <= 25\n"


class T(unittest.TestCase):
    def test_ops_asr_brief_needs_device_line(self):
        base = "owner: ops\nFiles (only these): a.py\nDone: ok\nsee a.py:1\nturns <= 5\nrun harvest transcription\n"
        self.assertTrue(any("Device" in m for m in brieflint.lint(base)))
        self.assertFalse(any("Device" in m for m in brieflint.lint(base + "Device: cuda\n")))
        self.assertFalse(any("Device" in m for m in brieflint.lint(base.replace("harvest transcription", "ls"))))

    def test_untracked_predicted_path_flagged_in_repo(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo = td.name
        subprocess.run(["git", "-C", repo, "init", "-q"], check=True)
        for n in ("tracked.py", "loose.py"):
            with open(os.path.join(repo, n), "w", encoding="utf-8") as f:
                f.write("x\n")
        subprocess.run(["git", "-C", repo, "add", "tracked.py"], check=True)
        text = GOOD + "Predicted paths: tracked.py, loose.py, new.py\n"
        got = brieflint.lint(text, repo)
        self.assertEqual(len(got), 1, got)
        self.assertIn("loose.py", got[0])
        self.assertIn("untracked", got[0])  # new.py does not exist yet: not flagged
        self.assertEqual(brieflint.lint(text), [])  # no repo: nothing to check

    def test_good_ok(self):
        self.assertEqual(brieflint.lint(GOOD), [])

    def test_missing_qrun(self):
        self.assertTrue(any("qrun" in m for m in brieflint.lint(GOOD.replace("qrun.py", "x"))))

    def test_ops_exempt_from_qrun(self):
        self.assertFalse(any("qrun" in m for m in brieflint.lint("owner: ops(haiku)\n" + GOOD.replace("qrun.py", "x"))))

    def test_tail(self):
        self.assertTrue(any("tail" in m for m in brieflint.lint(GOOD + "npm test | tail -5")))

    def test_no_allow(self):
        self.assertTrue(any("predicted" in m for m in brieflint.lint(GOOD.replace("Files (only these)", "F"))))

    def test_cs_qrun_test_line_accepted(self):
        text = (
            "Files (only these): web/a.js\nLoop: cs-qrun -- node --test x\nDone: suite green. see web/a.js:24. turns <= 25\n"
            "Test: cs-qrun -- node --test web/a.test.js\n"
        )
        self.assertFalse(any("qrun" in m for m in brieflint.lint(text)))
        self.assertTrue(any("no qrun.py or cs-qrun" in m for m in brieflint.lint("Test: node --test a.js\n")))

    def test_test_line_unquoted_glob_flagged(self):
        base = "Files (only these): a.py\nLoop: cs-qrun -- x\nDone: ok. see a.py:1. turns <= 5\n"
        flagged = brieflint.lint(base + "Test: cs-qrun -- python3 -m pytest tests/core/*.py\n")
        self.assertTrue(any("glob" in m for m in flagged))
        self.assertFalse(any("glob" in m for m in brieflint.lint(base + "Test: cs-qrun -- python3 -m pytest tests/core\n")))
        self.assertFalse(any("glob" in m for m in brieflint.lint(base + 'Test: cs-qrun -- python3 -m pytest -k "a or b*"\n')))

    def test_abs_cd(self):
        self.assertTrue(any("absolute cd" in m for m in brieflint.lint(GOOD + "cd /workspace/x")))

    def test_abs_workspace_test_path(self):
        bad = GOOD + "\nTest: python3 -I /root/.claude/tools/qrun.py -- node --test /workspace/r/tests/a.test.js\n"
        self.assertTrue(any("absolute workspace path" in m for m in brieflint.lint(bad)))
        ok = GOOD + "\nTest: python3 -I /root/.claude/tools/qrun.py -- test -s /workspace/.work/s/a.md\n"
        self.assertFalse(any("absolute workspace path" in m for m in brieflint.lint(ok)))

    def test_test_line_absolute_main_checkout_path(self):
        repo = "/srv/main/example-app"
        bad = GOOD + "Test: python3 -I /root/.claude/tools/qrun.py -- node --test /srv/main/example-app/web/a.test.js\n"
        self.assertTrue(any("main checkout" in m for m in brieflint.lint(bad, repo)))
        self.assertFalse(any("main checkout" in m for m in brieflint.lint(bad)))  # no repo: nothing to compare

    def test_test_line_tool_path_is_not_main_checkout(self):
        home = os.path.dirname(TOOLS_DIR)
        good = GOOD + f"Test: python3 -I {TOOLS_DIR}/qrun.py -- node --test web/a.test.js\n"
        self.assertFalse(any("main checkout" in m for m in brieflint.lint(good, home)))

    def test_owner_label_needs_owner_text_path(self):
        self.assertTrue(any("owner text" in m for m in brieflint.lint(GOOD + "Scope per A3.\n")))
        self.assertTrue(any("owner text" in m for m in brieflint.lint(GOOD + "Follow the owner rule.\n")))
        self.assertFalse(any("owner text" in m for m in brieflint.lint(GOOD + "Scope per A3, see docs/owners.md.\n")))
        self.assertFalse(any("owner text" in m for m in brieflint.lint(GOOD)))

    def test_no_budget(self):
        self.assertTrue(any("budget" in m for m in brieflint.lint(GOOD.replace("turns <= 25", ""))))

    def test_pattern_without_line_names_fix(self):
        msgs = brieflint.lint(GOOD.replace("web/a.js:24", "x") + "\nPattern: web/b.js\n")
        self.assertTrue(any("`Pattern: web/b.js` has no line" in m and "grep -n" in m and "web/b.js:<line>" in m for m in msgs))

    def test_no_anchor(self):
        self.assertTrue(any("anchor" in m for m in brieflint.lint(GOOD.replace("web/a.js:24", "x"))))

    def test_alt_wording(self):
        alt = GOOD.replace("Files (only these)", "Predicted paths").replace("Done:", "Done when:").replace("turns <= 25", "turns: 25")
        self.assertEqual(brieflint.lint(alt), [])

    def test_agent_file_skipped(self):
        self.assertTrue(brieflint.is_agent_file(os.path.join(AGENTS_DIR, "x.md")))

    def test_non_agent_file_linted(self):
        self.assertFalse(brieflint.is_agent_file(os.path.join(TOOLS_DIR, "x.md")))

    def test_bad_brief_elsewhere_fails(self):
        import subprocess

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("bad\n")
        self.addCleanup(os.unlink, f.name)
        r = subprocess.run([sys.executable, "-I", brieflint.__file__, f.name], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 1)

    def test_ctxpack(self):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write("a\nb\nc\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(ctxpack.pack(f.name + ":2-3").split("\n")[1:], ["2\tb", "3\tc"])

    def test_grep_c_in_test_line(self):
        bad = GOOD + "Test: python3 -I /root/.claude/tools/qrun.py -- grep -c foo web/a.js\n"
        self.assertTrue(any("grep -c" in m for m in brieflint.lint(bad)))
        ok = GOOD + "Test: python3 -I /root/.claude/tools/qrun.py -- ! grep -q foo web/a.js\n"
        self.assertFalse(any("grep -c" in m for m in brieflint.lint(ok)))

    def test_jq_path_checked_against_a_readable_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "c.json"), "w", encoding="utf-8") as f:
                f.write('{"a": {"b": 1}}')
            t = GOOD + "Test: python3 -I /root/.claude/tools/qrun.py -- jq -e '.a.x' c.json\n"
            self.assertTrue(any("jq path .a.x" in m for m in brieflint.lint(t, d)))
            self.assertFalse(any("jq" in m for m in brieflint.lint(t.replace(".a.x", ".a.b"), d)))
            self.assertFalse(any("jq" in m for m in brieflint.lint(t.replace("c.json", "gone.json"), d)))
            self.assertFalse(any("jq" in m for m in brieflint.lint(t)))  # no repo: no check

    def test_jq_only_leading_chain_without_pipe(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "c.json"), "w", encoding="utf-8") as f:
                f.write('{"a": {"b": 1}, "k-x": 1}')
            head = GOOD + "Test: python3 -I /root/.claude/tools/qrun.py -- jq -e "

            def bad(filt, name="c.json"):
                return any("jq path" in m for m in brieflint.lint(head + filt + " " + name + "\n", d))

            self.assertFalse(bad("'.a | .zz'"))
            self.assertFalse(bad("'.a.b, .zz'"))
            self.assertFalse(bad("'map(.zz)'"))
            self.assertFalse(bad("'.a.b == \".zz\"'"))
            self.assertFalse(bad("'.[\"k-x\"]'"))
            self.assertFalse(bad("'.a.b-1'"))
            self.assertFalse(bad("'.a.1x'"))
            self.assertFalse(bad("'.zz'", "/etc/c.json"))
            self.assertFalse(bad("'.zz'", "../c.json"))
            self.assertTrue(bad("'.a.x'"))

    def test_jq_skips_big_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "big.json"), "w", encoding="utf-8") as f:
                f.write('{"a": 1, "pad": "' + "x" * 1_100_000 + '"}')
            t = GOOD + "Test: python3 -I /root/.claude/tools/qrun.py -- jq -e '.zz' big.json\n"
            self.assertFalse(any("jq" in m for m in brieflint.lint(t, d)))


class TestLeadVerifiedBrief(unittest.TestCase):
    def test_dev_brief_note_counts_marked_lines_only(self):
        f = os.path.join(tracked_mkdtemp(), "task-1.md")
        with open(f, "w") as h:
            h.write("Goal: check `lead-verified` lines\nDone: suite green. lead-verified\nTest: x\n")
        self.assertIn("1 line(s) of the task file are marked lead-verified", briefs.lead_note(f))
        with open(f, "w") as h:
            h.write("Goal: check `lead-verified` lines\nDone: suite green\nTest: x\n")
        self.assertEqual(briefs.lead_note(f), "")

    def test_docs_index_line_needs_marker(self):
        d = tracked_mkdtemp()
        with open(os.path.join(d, "task-1.md"), "w") as h:
            h.write("Test: python3 .claude/tools/docs_index.py\n")
        with open(os.path.join(d, "task-2.md"), "w") as h:
            h.write("Test: cs-docs-index --check  # lead-verified\n")
        self.assertEqual(len(plan_check.check_lead_verified(d, "task-1")), 1)
        self.assertEqual(plan_check.check_lead_verified(d, "task-2"), [])


if __name__ == "__main__":
    unittest.main()
