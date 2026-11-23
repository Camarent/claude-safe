"""plan_commands: executable and path arguments of done_when / Test / gate commands are checked before the run, without running them."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import configuration, plan_commands, public_words


class T(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.repo = td.name

    def make(self, *paths):
        """Create files (a trailing / makes a dir) under the repo; each file is git-tracked, so the dir is a repo dir."""
        for p in paths:
            full = os.path.join(self.repo, p)
            if p.endswith("/"):
                os.makedirs(full, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                open(full, "w", encoding="utf-8").close()
        subprocess.run(["git", "-C", self.repo, "init", "-q"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True, capture_output=True)

    def findings(self, cmd, produced=(), nested=()):
        return plan_commands.command_findings("t-1 Test", cmd, self.repo, list(produced), list(nested))

    def test_work_dir_path_is_not_missing(self):
        self.make("src/a.txt")
        self.assertEqual(self.findings("cs-qrun -- test -f .work/s/out.txt"), [])

    def test_bad_executable_is_problem(self):
        got = self.findings("./tool x")
        self.assertEqual(len(got), 1)
        self.assertIn("runs ./tool, which does not exist", got[0])
        self.assertFalse(got[0].startswith(plan_commands.WARN_PREFIX))

    def test_bad_argument_path_is_problem(self):
        self.make("tool.py")
        got = self.findings("python3 tool.py a/missing.txt")
        self.assertEqual(len(got), 1)
        self.assertIn("argument a/missing.txt does not exist", got[0])

    def test_ok_paths_pass(self):
        self.make("tool.py", "data/in.txt")
        self.assertEqual(self.findings("python3 tool.py data/in.txt"), [])

    def test_plan_created_path_is_exempt(self):
        self.assertEqual(self.findings("python3 out/new.py", produced=["out/new.py"]), [])
        self.assertEqual(self.findings("make/new.sh x/y.txt", produced=["make/new.sh", "x/y.txt"]), [])

    def test_output_option_value_and_build_dir_exempt(self):
        self.make("tool.py")
        self.assertEqual(self.findings("python3 tool.py --out res/report.json"), [])
        self.assertEqual(self.findings("python3 tool.py target/x.bin"), [])

    def test_bare_word_one_match_suggests(self):
        self.make("workspace/course-a/x.txt", "tool")
        got = self.findings("./tool validate course-a")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(plan_commands.WARN_PREFIX))
        self.assertIn("did you mean workspace/course-a?", got[0])

    def test_bare_word_two_matches_no_suggestion(self):
        self.make("workspace/course-a/a.txt", "other/course-a/b.txt", "tool")
        self.assertEqual(self.findings("./tool validate course-a"), [])

    def test_bare_word_after_option_no_suggestion(self):
        self.make("workspace/dojo-core/x.txt", "tool")
        self.assertEqual(self.findings("./tool -p dojo-core"), [])
        self.assertEqual(self.findings("./tool --name dojo-core"), [])
        self.assertEqual(self.findings("cs-qrun -- ./tool -p dojo-core hint_leak"), [])

    def test_system_tools_no_suggestion(self):
        self.make("workspace/course-a/x.txt", "workspace/hint_leak/y.txt")
        for cmd in ("cargo test -p dojo-core hint_leak", "npm run course-a", "git status course-a", "pytest -k course-a"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.findings(cmd), [])

    def test_glob_quote_variable_and_pipe_skipped(self):
        self.make("tool.py", "data/a.py")
        self.assertEqual(self.findings("python3 tool.py data/*.py"), [])
        self.assertEqual(self.findings("python3 tool.py $HOME/x/y"), [])
        self.assertEqual(self.findings("python3 tool.py 'a/b.txt'"), [])
        self.assertEqual(self.findings("python3 tool.py missing/x.txt | tail -1"), [])

    def test_wrapper_stripping(self):
        self.make("tool")
        self.assertEqual(self.findings("cs-qrun -- ./tool x"), [])
        self.assertEqual(self.findings("python3 -I tool"), [])
        got = self.findings("cs-qrun -- ./gone x")
        self.assertEqual(len(got), 1)
        self.assertIn("runs ./gone", got[0])

    def test_nested_repo_root_and_cd(self):
        self.make("workspace/course-a/bin/run.sh", "workspace/course-a/.git/")
        nested = os.path.join(self.repo, "workspace/course-a")
        self.assertEqual(self.findings("bin/run.sh", nested=[nested]), [])
        self.assertEqual(self.findings("cd workspace/course-a && bin/run.sh"), [])
        self.assertEqual(len(self.findings("bin/run.sh")), 1)  # not in the main repo: no nested root given

    def test_git_and_gh_arguments_are_refs_not_paths(self):
        self.make("tool")
        for cmd in (
            "git tag refs/tags/v0.1.0 HEAD",
            "git tag -d refs/tags/backup/pre-public-2026-11",
            "git diff origin/master...HEAD",
            "git checkout stage/some-feature",
            "git log HEAD~1",
            "git rev-parse HEAD^{tree}",
            "git log master..dev",
            "git show v1.2.3",
            "git push origin job/x/y",
            "cs-qrun -- git log origin/master...HEAD",
            "gh pr view job/x/y",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.findings(cmd), [])

    def test_git_executable_path_still_checked(self):
        got = self.findings("./git-hook log")
        self.assertEqual(len(got), 1)
        self.assertIn("runs ./git-hook, which does not exist", got[0])

    def test_ref_markers_skipped_in_any_command(self):
        self.make("tool")
        for cmd in ("./tool refs/tags/v1", "./tool HEAD~1", "./tool master..dev", "python3 tool origin/x...y"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.findings(cmd), [])

    def test_missing_arg_of_local_script_is_problem(self):
        self.make("tool.py")
        got = self.findings("python3 tool.py gone/x.txt")
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0].startswith(plan_commands.WARN_PREFIX))
        self.assertIn("argument gone/x.txt does not exist", got[0])

    def test_missing_arg_of_system_tool_is_warning_only(self):
        self.make("tool")
        for cmd in ("cargo test --manifest-path crates/x/Cargo.toml", "npm run build -- gone/x.json"):
            with self.subTest(cmd=cmd):
                got = self.findings(cmd)
                self.assertEqual(len(got), 1)
                self.assertTrue(got[0].startswith(plan_commands.WARN_PREFIX))


class PublicWords(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.repo = td.name
        subprocess.run(["git", "-C", self.repo, "init", "-q"], check=True, capture_output=True)
        self.write("README.md", "Intro line\nThe Secret Sauce is here\n")
        self.write("docs/guide.md", "nothing\nsecret again\n")
        self.write("code.py", "secret = 1\n")  # not a *.md file: never scanned
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True, capture_output=True)

    def write(self, rel, text):
        full = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def test_setting_default_is_empty_list(self):
        self.assertEqual(configuration.PUBLIC_FORBIDDEN_WORDS_DEFAULT, [])
        self.assertEqual(configuration.PUBLIC_FORBIDDEN_WORDS_KEY, "public_forbidden_words")

    def test_hit_names_file_and_line(self):
        got = public_words.hits(self.repo, ["sauce"])
        self.assertEqual(got, ["README.md:2: sauce"])

    def test_match_is_case_insensitive(self):
        got = public_words.hits(self.repo, ["SECRET"])
        self.assertEqual(sorted(got), ["README.md:2: SECRET", "docs/guide.md:2: SECRET"])

    def test_empty_word_list_finds_nothing(self):
        self.assertEqual(public_words.hits(self.repo, []), [])

    def test_blank_word_does_not_match_every_line(self):
        # mutation check: an empty pattern would match every line of every doc
        self.assertEqual(public_words.hits(self.repo, ["", "   "]), [])
        self.assertEqual(public_words.hits(self.repo, ["", "sauce"]), ["README.md:2: sauce"])

    def test_untracked_or_non_markdown_not_scanned(self):
        self.assertEqual(public_words.hits(self.repo, ["secret = 1"]), [])


if __name__ == "__main__":
    unittest.main()
