"""prepare_repo: pipeline-local files go into .git/info/exclude, once, in the repo and in nested repos (pipeline.json `nested_repo_excludes`)."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import prepare_repo
from pipeline.configuration import PIPELINE_EXCLUDES, clear_cache

NESTED_EXCLUDES = ["materials/", "sources/*/pages/", "sources/*/photos/"]  # the owner's old values, set in pipeline.json by the tests that need them


def git(*a):
    subprocess.run(["git", *a], check=True, capture_output=True)


def lines(repo):
    with open(os.path.join(repo, ".git", "info", "exclude")) as fh:
        return fh.read().splitlines()


def ignore_lines(repo):
    with open(os.path.join(repo, ".gitignore")) as fh:
        return fh.read().splitlines()


class PrepareRepoTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.repo = os.path.join(t.name, "r")
        os.makedirs(os.path.join(self.repo, "course"))
        git("init", "-q", self.repo)
        git("init", "-q", os.path.join(self.repo, "course"))
        self.set_excludes(NESTED_EXCLUDES)

    def set_excludes(self, value):
        with open(os.path.join(self.repo, "pipeline.json"), "w") as fh:
            json.dump({"nested_repo_excludes": value} if value is not None else {}, fh)
        clear_cache()

    def test_adds_pipeline_files_once_and_keeps_existing_lines(self):
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a") as fh:
            fh.write("mine/\n")
        added = prepare_repo.prepare(self.repo)
        self.assertTrue(set(PIPELINE_EXCLUDES) <= set(lines(self.repo)))
        self.assertIn("mine/", lines(self.repo))
        self.assertTrue(added)
        before = lines(self.repo)
        self.assertEqual(prepare_repo.prepare(self.repo), [])  # idempotent
        self.assertEqual(lines(self.repo), before)

    def test_nested_repo_gets_configured_entries_only_there(self):
        prepare_repo.prepare(self.repo)
        course = os.path.join(self.repo, "course")
        self.assertTrue(set(NESTED_EXCLUDES) <= set(lines(course)))
        self.assertFalse(set(NESTED_EXCLUDES) & set(lines(self.repo)))
        self.assertTrue(set(NESTED_EXCLUDES) <= set(ignore_lines(course)))
        self.assertFalse(set(NESTED_EXCLUDES) & set(ignore_lines(self.repo)))

    def test_default_no_nested_entries(self):
        self.set_excludes(None)  # no pipeline.json key: the default [] writes nothing into nested repos
        prepare_repo.prepare(self.repo)
        course = os.path.join(self.repo, "course")
        self.assertFalse(set(NESTED_EXCLUDES) & set(lines(course)))
        self.assertFalse(os.path.exists(os.path.join(course, ".gitignore")))

    def test_file_without_trailing_newline(self):
        p = os.path.join(self.repo, ".git", "info", "exclude")
        with open(p, "w") as fh:
            fh.write("mine/")
        prepare_repo.prepare(self.repo)
        self.assertIn("mine/", lines(self.repo))

    def test_gitignore_created_when_absent(self):
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".gitignore")))
        prepare_repo.prepare(self.repo)
        self.assertTrue(set(PIPELINE_EXCLUDES) <= set(ignore_lines(self.repo)))

    def test_gitignore_appends_missing_entries_without_duplicates(self):
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write("build/\n" + PIPELINE_EXCLUDES[0] + "\n")
        added = prepare_repo.prepare(self.repo)
        got = ignore_lines(self.repo)
        self.assertEqual(got[:2], ["build/", PIPELINE_EXCLUDES[0]])  # old text kept, not moved
        self.assertEqual(got.count(PIPELINE_EXCLUDES[0]), 1)
        self.assertEqual(got[2], "")  # blank line before the block
        self.assertEqual(sorted(got[3:]), sorted(set(PIPELINE_EXCLUDES) - {PIPELINE_EXCLUDES[0]}))
        self.assertIn(".gitignore: " + PIPELINE_EXCLUDES[1], added)
        self.assertNotIn(".gitignore: " + PIPELINE_EXCLUDES[0], added)

    def test_gitignore_without_trailing_newline_gets_blank_line(self):
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write("build/")
        prepare_repo.prepare(self.repo)
        got = ignore_lines(self.repo)
        self.assertEqual(got[:2], ["build/", ""])
        self.assertTrue(set(PIPELINE_EXCLUDES) <= set(got))

    def test_second_run_reports_already_prepared(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prepare_repo.main(["--dir", self.repo])
        self.assertIn("added: ", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prepare_repo.main(["--dir", self.repo])
        self.assertEqual(out.getvalue().splitlines()[0], "already prepared")

    def test_not_a_repo_raises(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(RuntimeError):
            prepare_repo.prepare(d)


def write(repo, name, text):
    with open(os.path.join(repo, name), "w") as fh:
        fh.write(text)


def snapshot(repo):
    out = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != "objects"]
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[p] = fh.read()
    return out


class PrepareExtrasTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.repo = os.path.join(t.name, "r")
        os.makedirs(self.repo)
        git("init", "-q", self.repo)
        clear_cache()

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prepare_repo.main(["--dir", self.repo, *args])
        clear_cache()
        return out.getvalue()

    def test_dry_run_prints_and_writes_nothing(self):
        write(self.repo, "pyproject.toml", "")
        os.makedirs(os.path.join(self.repo, "docs"))
        before = snapshot(self.repo)
        text = self.run_main("--dry-run", "--add-worktrees")
        self.assertIn("would add:", text)
        self.assertIn(".gitignore: __pycache__/", text)
        self.assertIn("CLAUDE.md: ## Docs", text)
        self.assertIn('pipeline.json: "worktrees": true', text)
        self.assertEqual(snapshot(self.repo), before)

    def test_language_ignores_only_for_detected_languages_and_idempotent(self):
        write(self.repo, "package.json", "{}")
        write(self.repo, ".gitignore", "dist/\nmine\n")
        self.run_main()
        got = ignore_lines(self.repo)
        self.assertEqual(got[:2], ["dist/", "mine"])
        self.assertIn("node_modules/", got)
        self.assertEqual(got.count("dist/"), 1)
        self.assertNotIn("__pycache__/", got)
        self.assertNotIn("target/", got)
        before = snapshot(self.repo)
        self.assertEqual(self.run_main().splitlines()[0], "already prepared")
        self.assertEqual(snapshot(self.repo), before)

    def test_rust_and_python_markers(self):
        write(self.repo, "Cargo.toml", "")
        write(self.repo, "requirements.txt", "")
        self.run_main()
        self.assertTrue({"target/", "__pycache__/", "*.pyc", ".venv/"} <= set(ignore_lines(self.repo)))
        self.assertNotIn("node_modules/", ignore_lines(self.repo))

    def test_docs_section_written_excluded_and_idempotent(self):
        os.makedirs(os.path.join(self.repo, "docs"))
        self.run_main()
        with open(os.path.join(self.repo, "CLAUDE.md")) as fh:
            self.assertIn("## Docs", fh.read())
        self.assertIn("CLAUDE.md", lines(self.repo))
        before = snapshot(self.repo)
        self.run_main()
        self.assertEqual(snapshot(self.repo), before)

    def test_no_docs_folder_no_claude_file(self):
        self.run_main()
        self.assertFalse(os.path.exists(os.path.join(self.repo, "CLAUDE.md")))

    def test_existing_docs_section_and_tracked_claude_untouched(self):
        os.makedirs(os.path.join(self.repo, "docs"))
        write(self.repo, "CLAUDE.md", "# x\n\n## Docs\nsee elsewhere\n")
        key = os.path.join(self.repo, "CLAUDE.md")
        before = snapshot(self.repo)[key]
        self.run_main()
        self.assertEqual(snapshot(self.repo)[key], before)
        self.assertNotIn("CLAUDE.md", lines(self.repo))
        write(self.repo, "CLAUDE.md", "# tracked\n")
        git("-C", self.repo, "add", "-f", "CLAUDE.md")
        self.run_main()
        with open(os.path.join(self.repo, "CLAUDE.md")) as fh:
            self.assertEqual(fh.read(), "# tracked\n")

    def test_worktrees_reported_then_added_without_touching_values(self):
        write(self.repo, "pipeline.json", '{\n  "max_tasks": 3\n}\n')
        self.assertIn("report: pipeline.json: no `worktrees`", self.run_main())
        self.run_main("--add-worktrees")
        with open(os.path.join(self.repo, "pipeline.json")) as fh:
            text = fh.read()
        self.assertEqual(json.loads(text), {"worktrees": True, "max_tasks": 3})
        self.assertIn('"max_tasks": 3\n}', text)
        self.assertNotIn("no `worktrees`", self.run_main("--add-worktrees"))

    def test_worktrees_existing_false_kept_and_empty_object(self):
        write(self.repo, "pipeline.json", '{"worktrees": false}')
        self.run_main("--add-worktrees")
        with open(os.path.join(self.repo, "pipeline.json")) as fh:
            self.assertEqual(fh.read(), '{"worktrees": false}')
        write(self.repo, "pipeline.json", "{}")
        self.run_main("--add-worktrees")
        with open(os.path.join(self.repo, "pipeline.json")) as fh:
            self.assertEqual(json.loads(fh.read()), {"worktrees": True})

    def test_malformed_audit_lines_reported_never_rewritten(self):
        block = "# t\n```audit-tools\n# c\nruff | . | ruff check .\nok | . | a | p\n```\n"
        write(self.repo, "AUDIT.md", block)
        text = self.run_main()
        self.assertIn("AUDIT.md:4: audit-tools line has 3 fields", text)
        self.assertNotIn("AUDIT.md:5", text)
        with open(os.path.join(self.repo, "AUDIT.md")) as fh:
            self.assertEqual(fh.read(), block)

    def test_no_audit_block_nothing_invented(self):
        write(self.repo, "AUDIT.md", "# only text\n")
        text = self.run_main()
        self.assertNotIn("audit-tools", text)
        with open(os.path.join(self.repo, "AUDIT.md")) as fh:
            self.assertEqual(fh.read(), "# only text\n")

    def test_toolchain_missing_tool_and_builtin_skipped(self):
        write(
            self.repo, "pipeline.json", json.dumps({"gates": [{"name": "t", "cmd": "cd sub && FOO=1 no-such-tool-xyz --x"}, {"name": "u", "cmd": "echo hi"}]})
        )
        text = self.run_main()
        self.assertIn("report: gate `t`: `no-such-tool-xyz` is not on PATH", text)
        self.assertNotIn("`cd`", text)
        self.assertNotIn("gate `u`", text)

    def test_toolchain_pytest_not_importable(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "fakepy")
            write(d, "fakepy", "#!/bin/sh\nexit 1\n")
            os.chmod(fake, 0o755)
            write(self.repo, "pipeline.json", json.dumps({"gates": [{"name": "t", "cmd": f"{fake} -m pytest"}]}))
            self.assertIn("cannot import `pytest`", self.run_main())

    def test_crlf_files_keep_their_line_ending(self):
        with open(os.path.join(self.repo, ".gitignore"), "wb") as fh:
            fh.write(b"build/\r\n")
        with open(os.path.join(self.repo, "pipeline.json"), "wb") as fh:
            fh.write(b'{\r\n  "a": 1\r\n}\r\n')
        write(self.repo, "pyproject.toml", "")
        self.run_main("--add-worktrees")
        for name in (".gitignore", "pipeline.json"):
            with open(os.path.join(self.repo, name), "rb") as fh:
                data = fh.read()
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), name)  # no bare LF
        self.assertEqual(json.load(open(os.path.join(self.repo, "pipeline.json")))["worktrees"], True)

    def test_an_existing_claude_md_is_never_edited_only_reported(self):
        os.makedirs(os.path.join(self.repo, "docs"))
        write(self.repo, "CLAUDE.md", "# my notes\n")
        text = self.run_main()
        self.assertEqual(open(os.path.join(self.repo, "CLAUDE.md")).read(), "# my notes\n")
        self.assertNotIn("CLAUDE.md", "\n".join(lines(self.repo)))
        self.assertIn("report: CLAUDE.md: no `## Docs` section", text)

    def test_ignore_lines_match_by_directory_negations_win_and_tracked_dirs_stay(self):
        write(self.repo, "package.json", "{}")
        write(self.repo, ".gitignore", "/node_modules\n!dist/\n")
        self.run_main()
        self.assertEqual(ignore_lines(self.repo).count("node_modules/"), 0)
        self.assertNotIn("dist/", ignore_lines(self.repo))
        os.makedirs(os.path.join(self.repo, "target"))
        write(self.repo, "Cargo.toml", "")
        write(self.repo, "target/keep.txt", "x")
        git("-C", self.repo, "add", "-f", "target/keep.txt")
        self.run_main()
        self.assertNotIn("target/", ignore_lines(self.repo))  # the repo tracks it

    def test_toolchain_words_skip_keywords_variables_and_subshell_parens(self):
        from pipeline.prepare_checks import _first_words

        cases: tuple[tuple[str, list[str]], ...] = (
            ("(cd a && make)", ["make"]),
            ("for i in a; do foo; done", ["foo"]),
            ("! grep x f", ["grep"]),
            ('bash -c "a && b"', ["bash"]),
            ('cd "a b" && nosuchtool', ["nosuchtool"]),  # quotes no longer hide later commands
            ('VAR="x" ; python3 -m pytest', ["python3"]),  # a lone `;` is not a program
            ("pytest; ruff 'a;b'", ["pytest", "ruff"]),
            ("while read l; do :; done < f", []),  # a redirect is not a program
            ("x 2>&1 | y", ["x", "y"]),
            ("pytest a#b && ruff", ["pytest", "ruff"]),  # `#` inside a word is not a comment
            ("2>&1 pytest", ["pytest"]),
            ("FOO=1 2>/dev/null pytest", ["pytest"]),
        )
        for cmd, want in cases:
            self.assertEqual([w for w, _ in _first_words(cmd)], want, cmd)

    def test_a_tracked_claude_md_deleted_from_the_tree_is_not_recreated(self):
        os.makedirs(os.path.join(self.repo, "docs"))
        write(self.repo, "CLAUDE.md", "# notes\n")
        git("-C", self.repo, "add", "CLAUDE.md")
        os.remove(os.path.join(self.repo, "CLAUDE.md"))
        self.run_main()
        self.assertFalse(os.path.exists(os.path.join(self.repo, "CLAUDE.md")))

    def test_an_anchored_negation_stops_the_ignore_line(self):
        write(self.repo, "Cargo.toml", "")
        write(self.repo, ".gitignore", "!/target/\n")
        self.run_main()
        self.assertNotIn("target/", ignore_lines(self.repo))

    def test_the_probe_sees_pythonpath_like_the_gate_does(self):
        with tempfile.TemporaryDirectory() as libs:
            write(libs, "zzmod.py", "")
            from pipeline import prepare_checks

            with mock.patch.dict(os.environ, {"PYTHONPATH": libs}):
                self.assertTrue(prepare_checks._probe(sys.executable, "zzmod"))
            self.assertFalse(prepare_checks._probe(sys.executable, "zzmod"))

    def test_the_import_probe_does_not_run_project_code(self):
        marker = os.path.join(self.repo, "ran.txt")
        write(self.repo, "pytest.py", f"open({marker!r}, 'w').write('x')\n")
        write(self.repo, "pipeline.json", json.dumps({"gates": [{"name": "t", "cmd": f"{sys.executable} -m pytest"}]}))
        old = os.getcwd()
        os.chdir(self.repo)
        try:
            self.run_main()
        finally:
            os.chdir(old)
        self.assertFalse(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()
