import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmd_guard

Q = "python3 -I /root/.claude/tools/qrun.py --"


class CmdGuardTest(unittest.TestCase):
    def test_sed_is_allowed_only_to_print_lines(self):
        snip = '"/usr/local/bin/snip" run -- '
        for ok in ("sed -n '1,5p' f", "sed -n '10p' a b", "sed -n '3,$p' f | head", "ls | sed -n '1p'", snip + "sed -n '1,5p' f"):
            self.assertEqual(cmd_guard.verdict(ok), "", ok)
        for bad in ("sed -n -e '1w x' f", "sed -n '1,5p;w x' f", "sed -n '1,5p' -i f", "sed -n '/a/p' f", "sed -n 'w x' f", snip + "sed -n '1w x' f"):
            self.assertIn("`sed -n` is allowed only", cmd_guard.verdict(bad), bad)
        self.assertEqual(cmd_guard.verdict("sed -i 's/a/b/' f"), "")  # not opened by the default allowlist; a project's own `sed *` rule decides

    def test_default_flags_allow_sed_print_only(self):
        from pipeline.configuration import BASH_ALLOW, SED_PRINT_ALLOW

        self.assertIn(SED_PRINT_ALLOW, BASH_ALLOW)
        self.assertNotIn("sed *", BASH_ALLOW)

    def test_every_bind_of_a_static_serve_must_be_loopback(self):
        ok = "python3 -m http.server --bind 127.0.0.1 8000"
        self.assertEqual(cmd_guard.verdict(ok), "")
        self.assertEqual(cmd_guard.verdict(ok + " --bind 127.0.0.1"), "")
        self.assertIn("must bind", cmd_guard.verdict(ok + " --bind 0.0.0.0"))
        self.assertIn("must bind", cmd_guard.verdict("python3 -m http.server --bind 0.0.0.0 --bind 127.0.0.1 8000"))

    def test_cd_prefix_before_qrun_allowed(self):
        self.assertEqual(cmd_guard.verdict(f"cd /tmp/wt && {Q} node --test x.js"), "")

    def test_default_flags_allow_cd(self):  # 487 of 999 sandbox denials were `cd <worktree> && ...`
        from pipeline.configuration import DEFAULT_FLAGS

        self.assertIn("'Bash(cd *)'", DEFAULT_FLAGS)

    def test_default_flags_allow_snip_and_build_tools(self):  # 308 headless denials: 124 `cd wt && snip run -- ...`, snip was not allowed
        from pipeline.configuration import DEFAULT_FLAGS, claude_flags

        for want in ("'Bash(snip *)'", "'Bash(/usr/local/bin/snip *)'", "'Bash(ctest *)'", "'Bash(make *)'", "'Bash(bash scripts/*)'"):
            self.assertIn(want, DEFAULT_FLAGS)
        self.assertIn("'Bash(zig *)'", claude_flags({"bash_allow": ["zig *"]}))
        self.assertNotIn("'Bash(cd *)'", claude_flags({"bash_allow": ["zig *"]}))

    def test_denies_env_before_more_runners(self):
        for runner in ("ctest", "make test", "python3 -I -m pytest", "bash scripts/dev.sh test", "python3 -I ptest.py"):
            self.assertIn("env", cmd_guard.verdict(f"X=1 {runner}"), runner)

    def test_runners_configurable(self):
        from pipeline.configuration import test_runners

        self.assertEqual(test_runners({"test_runners": ["zig test"]}), ("zig test",))
        self.assertIn(r"(?:\S*/)?ctest", test_runners({}))

    def test_cs_qrun_pipe_denied_like_python_form(self):
        for head in ("cs-qrun", "/root/.claude/bin/cs-qrun"):
            for tail in (" | tail -5", " && echo ok", "; echo done", " || true"):
                self.assertEqual("pipe" in cmd_guard.verdict(f"{head} -- pytest{tail}"), "pipe" in cmd_guard.verdict(f"{Q} pytest{tail}"), (head, tail))
        self.assertIn("pipe", cmd_guard.verdict("cs-qrun -- pytest | tail"))
        self.assertIn("pipe", cmd_guard.verdict("bash -c 'cs-qrun -- x | tail'"))
        self.assertEqual(cmd_guard.verdict("cd wt && cs-qrun -- pytest"), "")

    def test_cs_qrun_is_a_test_head(self):
        self.assertIn("env", cmd_guard.verdict("FOO=1 cs-qrun -- pytest"))
        self.assertEqual(cmd_guard.verdict('echo "cs-qrun -- x | tail"'), "")

    def test_every_container_cs_name_allowed_by_flags(self):
        from pipeline import cmdnames
        from pipeline.configuration import DEFAULT_FLAGS, GUARD_WRAPPERS, HOME, SERVE_WRAPPER

        index = cmdnames.load(HOME)
        wanted = [
            n for k in cmdnames.commands(index, "container") for n in cmdnames.names_for(index, k) if n.startswith(index["prefix"]) and n not in GUARD_WRAPPERS
        ]
        self.assertGreater(len(wanted), 20)
        for name in wanted:
            if name == SERVE_WRAPPER:  # servectl: allowed by subcommand only (test_servectl.py), never as a whole
                self.assertNotIn(f"'Bash({name} *)'", DEFAULT_FLAGS, name)
                continue
            self.assertIn(f"'Bash({name} *)'", DEFAULT_FLAGS, name)
        for k, e in index["commands"].items():
            if cmdnames.side_of(e) == "host":
                self.assertNotIn(f"'Bash({cmdnames.command_name(index, k)} *)'", DEFAULT_FLAGS)

    def test_denies_pipe_after_qrun(self):
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} node --test x.js | tail -5"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} pytest || true"))

    def test_denies_chain_after_qrun(self):
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} pytest; echo done"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} pytest && git status"))

    def test_read_only_command_may_follow_qrun_with_and(self):
        self.assertEqual(cmd_guard.verdict(f"{Q} x && ls d"), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} x && echo ok && wc -l f"), "")
        self.assertEqual(cmd_guard.verdict(f"cd d && {Q} x"), "")

    def test_read_only_chain_still_denies_other_operators(self):
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x ; ls"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x | tail"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x || true"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x && ls d | wc"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x && git status"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x && ls d; git status"))

    def test_denies_redirect_or_substitution_in_read_only_tail(self):
        for tail in ("echo y > file", "cat a > b", "ls $(rm -rf d)", "ls `cmd`", "ls <(cmd)", "cat <f", "echo y >> f", 'ls "$(rm -rf d)"'):
            self.assertIn("pipe", cmd_guard.verdict(f"{Q} x && {tail}"), tail)

    def test_denies_background_tail(self):
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x && tail -f log &"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x &"))

    def test_denies_substitution_in_qrun_tail(self):
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x && {Q} y $(rm -rf d)"))

    def test_allows_kept_chain_shapes(self):
        self.assertEqual(cmd_guard.verdict(f"cd d && {Q} x"), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} A && {Q} B"), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} x && ls d"), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} x && wc -l f"), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} x\n"), "")

    def test_qrun_message_names_the_rule(self):
        why = cmd_guard.verdict(f"{Q} x ; ls")
        self.assertIn("One qrun per Bash call", why)
        self.assertIn("`&&` is allowed only before another qrun call or a read-only command", why)
        self.assertIn("(ls, echo, wc, cat, head, tail, test)", why)

    def test_cd_git_denial_names_git_c_form(self):
        why = cmd_guard.verdict("cd /wt/x && git status --short")
        self.assertIn("`git -C /wt/x status --short`", why)

    def test_cd_git_denial_fallback_without_args(self):
        self.assertIn("`git -C /wt/x <args>`", cmd_guard.verdict("cd /wt/x && git"))

    def test_cd_qrun_denial_names_own_call(self):
        why = cmd_guard.verdict(f"cd /wt/x && {Q} x ; ls")
        self.assertIn("run the qrun call as its own Bash call from `/wt/x`", why)
        self.assertNotIn("Your command starts with", cmd_guard.verdict(f"{Q} x ; ls"))

    def test_denies_quoted_qrun_path_pipe(self):
        self.assertIn("pipe", cmd_guard.verdict('python3 -I "/w/tools/qrun.py" -- x | tail'))

    def test_denies_bash_c_wrapped_pipe(self):
        self.assertIn("pipe", cmd_guard.verdict("bash -c 'python3 -I /root/.claude/tools/qrun.py -- x | tail'"))
        self.assertIn("pipe", cmd_guard.verdict('sh -lc "/w/tools/qrun.py -- x && git status"'))

    def test_denies_pipe_on_new_line(self):
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x\n| tail"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} x \\\n| tail"))

    def test_denies_bare_qrun_path_pipe(self):
        self.assertIn("pipe", cmd_guard.verdict("/w/tools/qrun.py -- x | tail"))

    def test_denies_export_then_test(self):
        self.assertIn("export", cmd_guard.verdict("export X=1 && pytest"))

    def test_denies_timeout_env_prefix(self):
        self.assertIn("env", cmd_guard.verdict("timeout 5 env X=1 pytest"))

    def test_allows_cd_prefix_and_plain_run(self):
        self.assertEqual(cmd_guard.verdict(f"cd /w/repo && {Q} node --test t.js"), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} pytest -k 'a or b'"), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} pytest 2>&1"), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} pytest &> out.txt"), "")

    def test_ignores_operators_inside_quotes(self):
        self.assertEqual(cmd_guard.verdict(f"{Q} sh -c 'a; b | c'"), "")
        self.assertEqual(cmd_guard.verdict('echo "python3 -I qrun.py -- x | tail"'), "")

    def test_ignores_qrun_filename_used_as_data(self):
        self.assertEqual(cmd_guard.verdict("for spec in qrun.py:255 local.py:49; do echo $spec | cut -c1-9; done"), "")
        self.assertEqual(cmd_guard.verdict("grep qrun.py notes.md | head"), "")

    def test_ignores_heredoc_body_text(self):
        body = "cat >> notes.md <<'EOF'\nrule: nothing after qrun.py; no pipe | tail && more\nEOF"
        self.assertEqual(cmd_guard.verdict(body), "")
        self.assertEqual(cmd_guard.verdict(f"{Q} cat <<'EOF'\nbody | tail\nEOF"), "")
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} pytest | tail <<'EOF'\nx\nEOF"))

    def test_denies_env_prefix_on_test_command(self):
        self.assertIn("env", cmd_guard.verdict("FOO=1 python3 -I /root/.claude/tools/qrun.py -- pytest"))
        self.assertIn("env", cmd_guard.verdict("env FOO=1 node --test t.js"))
        self.assertIn("env", cmd_guard.verdict("cd x && FOO=1 pytest"))

    def test_allows_env_on_non_test_command(self):
        self.assertEqual(cmd_guard.verdict("FOO=1 ls"), "")
        self.assertEqual(cmd_guard.verdict("git log | head"), "")
        self.assertEqual(cmd_guard.verdict("export X=1 && echo ok"), "")

    def test_serve_must_bind_loopback(self):
        for cmd in ("python3 -m http.server 8000", "python3 -m http.server --bind 0.0.0.0 8000", "cd x && python3 -I -m http.server -b 0.0.0.0"):
            self.assertIn("127.0.0.1", cmd_guard.verdict(cmd), cmd)
        for cmd in ("python3 -m http.server --bind 127.0.0.1 8000", "cd x && python3 -I -m http.server --bind 127.0.0.1 8123"):
            self.assertEqual(cmd_guard.verdict(cmd), "", cmd)

    def test_flags_allow_loopback_serve_and_project_override(self):
        from pipeline.configuration import SERVE_HOST, claude_flags, ui_paths

        self.assertIn(f"'Bash(python3 -m http.server --bind {SERVE_HOST} *)'", claude_flags())
        self.assertNotIn("0.0.0.0", claude_flags())
        self.assertIn("'Bash(npm run serve *)'", claude_flags({"serve_allow": ["npm run serve *"]}))
        self.assertEqual(ui_paths({"ui_paths": ["*.rs"]}), ("*.rs",))
        self.assertIn("*.html", ui_paths())

    def test_agents_say_localhost_only_and_stop_server(self):
        from pipeline.configuration import AGENTS_DIR

        for name, stop in (("web-tester.md", "cs-servectl stop"), ("ux-reviewer.md", "cs-servectl stop")):
            with open(os.path.join(AGENTS_DIR, name), encoding="utf-8") as f:
                text = f.read()
            for want in ("--bind 127.0.0.1", stop, "UNVERIFIED"):
                self.assertIn(want, text, f"{name}: {want}")


class ImportFallbackTest(unittest.TestCase):
    def test_guard_denies_without_shellwords(self):
        import importlib.util

        for name in ("pipeline.shellwords", "pipeline.configuration"):
            self.addCleanup(sys.modules.__setitem__, name, sys.modules.get(name))  # type: ignore[arg-type]  # restores a possibly absent entry
        sys.modules["pipeline.shellwords"] = None  # type: ignore[assignment]  # makes `import` raise ImportError
        spec = importlib.util.spec_from_file_location("cmd_guard_noshellwords", cmd_guard.__file__)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(hasattr(mod, "FALLBACK_SPLIT"))
        self.assertIn("cannot import the pipeline package", mod.verdict(f"{Q} node --test x.js | tail -5"))  # fail closed: every command denied
        self.assertIn("cannot import the pipeline package", mod.verdict("ls -la"))
        self.assertIn("cannot import the pipeline package", mod.verdict(f"cd /tmp/wt && {Q} pytest"))


class HeredocStripTest(unittest.TestCase):
    def test_strip_heredoc_cases(self):
        cases = {
            "cat <<EOF\nx | y\nEOF\necho z": "cat <<EOF\necho z",
            "cat <<'EOF' > f\nx\nEOF": "cat <<'EOF' > f",
            'cat <<"EOF"\nx\n  EOF  \nnext': 'cat <<"EOF"\nnext',
            "cat <<-EOF\n\tx\n\tEOF\n": "cat <<-EOF\n",
            "cat <<A\nEOF\nA\nz": "cat <<A\nz",
            "cat <<EOF\nEOF": "cat <<EOF",
            "cat <<EOF\nnever ends": "cat <<EOF\nnever ends",
            "cat <<A\n<<B\nA\nB\nB": "cat <<A\nB\nB",
            "a <<X\n1\nX\nb <<Y\n2\nY": "a <<X\nb <<Y",
        }
        for cmd, want in cases.items():
            self.assertEqual(cmd_guard.strip_heredocs(cmd), want, cmd)

    def test_long_input_is_fast(self):
        import time

        big = "cat <<EOF\n" + "line | x && y\n" * 15000 + "EOF\necho done"
        unterminated = "echo <<EOF " * 3000 + "\n" + "word\n" * 20000
        for text in (big, unterminated):
            t = time.monotonic()
            cmd_guard.strip_heredocs(text)
            self.assertLess(time.monotonic() - t, 1.0)

    def test_bad_runner_entries_are_dropped(self):
        pat, bad = cmd_guard.compile_runners(["pytest", "(unclosed", "ctest"])
        self.assertEqual(bad, ["(unclosed"])
        self.assertTrue(pat.match("ctest -x") and pat.match("pytest"))
        self.assertIsNone(pat.match("(unclosed"))

    def test_guard_wrappers_not_in_bash_allow(self):  # agents never run the hook wrappers
        from pipeline.configuration import BASH_ALLOW, DEFAULT_FLAGS, GUARD_WRAPPERS

        self.assertEqual(GUARD_WRAPPERS, {"cs-cmd-guard", "cs-review-guard", "cs-model-guard"})
        for name in GUARD_WRAPPERS:
            self.assertNotIn(f"{name} *", BASH_ALLOW)
            self.assertNotIn(f"'Bash({name} *)'", DEFAULT_FLAGS)

    def test_quoted_snip_compound_shapes_allowed(self):  # real denials from a headless run: the wrapper was quoted, which the literal allow pattern missed
        import fnmatch

        from pipeline.configuration import BASH_ALLOW, claude_flags
        from pipeline.shellwords import split_commands

        cmds = (
            '"/usr/local/bin/snip" run -- ls .work/x; head -40 a.js',
            '"/usr/local/bin/snip" run -- git -C /workspace/example-app log --oneline -3 && cat .notes 2>/dev/null',
            'cd /workspace/.wt/x/task-5/example-app; "/usr/local/bin/snip" run -- grep -n "cloze" crates/a.rs | head -20',
            'cat /workspace/.work/x/task-5.md | head -60; "/usr/local/bin/snip" run -- ls',
            '"snip" run -- wc -l a.js',
        )
        for c in cmds:
            self.assertEqual(cmd_guard.verdict(c), "", c)
            for raw in split_commands(cmd_guard.strip_quotes(c)):
                seg = raw.strip().split(" 2>")[0]
                self.assertTrue(any(fnmatch.fnmatchcase(seg, pat) for pat in BASH_ALLOW), seg)
        self.assertIn("'Bash(\"/usr/local/bin/snip\" *)'", claude_flags())
        self.assertTrue(
            any(fnmatch.fnmatchcase("sed -n 1,2p a", pat) for pat in BASH_ALLOW)
        )  # `sed -n` print is allowed since 2026-11-17 (cmd_guard.sed_why denies every other sed form)
        self.assertFalse(any(fnmatch.fnmatchcase("sed 's/a/b/' a", pat) for pat in BASH_ALLOW))

    def test_plain_tools_and_python_inline_in_bash_allow(self):  # owner decision: headless roles may run these
        from pipeline.configuration import BASH_ALLOW, PLAIN_SHELL_TOOLS, PY_INLINE, PY_STDIN

        for t in ("mkdir", "cat", "head", "tail", "wc", "sort", "uniq", "cut", "echo", "ls", "jq", "grep"):
            self.assertIn(f"{t} *", BASH_ALLOW)
        for t in ("rm", "sed", "awk", "tee", "xargs", "find", "for", "while"):
            self.assertNotIn(t, PLAIN_SHELL_TOOLS)
            self.assertNotIn(f"{t} *", BASH_ALLOW)
        for entry in ("mkdir *", "cat *", f"{PY_INLINE} *", PY_STDIN, f"{PY_STDIN} *"):
            self.assertIn(entry, BASH_ALLOW)
        self.assertEqual(cmd_guard.verdict('python3 -I -c "import sys; print(1)"'), "")
        self.assertEqual(cmd_guard.verdict("mkdir -p out/x && cat out/x/f"), "")

    def test_cd_then_git_denied_with_rewrite(self):
        d = "/workspace/.wt/course-a-furigana/task-18/example-app"
        why = cmd_guard.verdict(f"cd {d} && git -c safe.directory='*' status --short")
        self.assertIn(f"git -C {d}", why)
        for cmd in (f"cd {d}; git status", f'cd {d} && "/usr/local/bin/snip" run -- git diff', "cd d && snip run -- git log"):
            self.assertIn("git -C", cmd_guard.verdict(cmd), cmd)

    def test_git_without_cd_and_cd_without_git_allowed(self):
        for cmd in ("git -C /w/d status", "git status", "cd d && ls", "cd d && grep -rn x .", f"cd d && {Q} pytest"):
            self.assertEqual(cmd_guard.verdict(cmd), "", cmd)

    def test_qrun_chain_allowed_but_pipe_and_semicolon_denied(self):
        self.assertEqual(cmd_guard.verdict(f"{Q} a && {Q} b"), "")
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} a | tail"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} a; git status"))
        self.assertEqual(cmd_guard.verdict(f"{Q} a && echo ok"), "")
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} a && git status"))
        self.assertIn("pipe", cmd_guard.verdict(f"{Q} a && {Q} b | tail"))

    def test_mktemp_allowed(self):
        from pipeline.configuration import BASH_ALLOW

        self.assertIn("mktemp *", BASH_ALLOW)


class CdGitRewriteTest(unittest.TestCase):
    def test_rewrites(self):
        cases = {
            "cd /w && git status": "git -C /w status",
            "cd /w; git status": "git -C /w status",
            "cd /w && git status | head": "git -C /w status | head",
            "cd d && git -c a=b -C sub log": "git -C d -c a=b -C sub log",
            "cd /w && snip run -- git diff": "snip run -- git -C /w diff",
            "cd a && git x && cd b && git y": "git -C a x && git -C b y",
            "cd /w && git commit -m 'a; b' && echo ok": "git -C /w commit -m 'a; b' && echo ok",
            "cd /w && git log && ls /abs": "git -C /w log && ls /abs",
        }
        for cmd, want in cases.items():
            self.assertEqual(cmd_guard.rewrite_cd_git(cmd), want, cmd)
            self.assertEqual(cmd_guard.verdict(want), "", want)

    def test_not_rewritten(self):
        for cmd in (
            "cd /w && git status && cat f",  # relative file
            "cd /w && git status && ls",  # no args
            "cd /w && git status > out.txt",  # relative redirect
            "cd ~/w && git status",
            "cd $W && git status",
            'cd "/w x" && git status',
            "cd /w || git status",
            "cd /w && git status &",
            "cd /w && git log && grep x y",
            "(cd /w && git status)",
        ):
            self.assertEqual(cmd_guard.rewrite_cd_git(cmd), "", cmd)

    def test_dependent_detection(self):
        for seg in ("ls", "cat f", "grep x /a", "ls ./a", "echo hi > f", "echo $(pwd)"):
            self.assertTrue(cmd_guard.cwd_dependent(seg) or seg.startswith("echo hi"), seg)
        for seg in ("echo hi", "ls /a /b", "true", "echo x 2>&1"):
            self.assertFalse(cmd_guard.cwd_dependent(seg), seg)
        self.assertFalse(cmd_guard.cwd_dependent("head -5", piped=True))
        self.assertTrue(cmd_guard.cwd_dependent("head -5"))

    def test_qrun_chain_still_denied_after_rewrite(self):
        cmd = f"cd /w && git status ; {Q} x"
        self.assertTrue(cmd_guard.verdict(cmd))
        self.assertEqual(self._run(cmd)["hookSpecificOutput"]["permissionDecision"], "deny")

    def _enter_cwd_worktree(self):
        """chdir into a fresh temp dir (the job worktree stand-in); restored and removed at cleanup."""
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        wt = os.path.realpath(tmp.name)
        old = os.getcwd()
        os.chdir(wt)
        self.addCleanup(os.chdir, old)
        return wt

    def test_cd_to_cwd_git_not_denied(self):  # C1: the cwd already IS the worktree, so the cd changes nothing
        wt = self._enter_cwd_worktree()
        self.assertEqual(cmd_guard.verdict(f"cd {wt} && git status --short"), "")
        self.assertEqual(cmd_guard.verdict("cd . && git status --short"), "")

    def test_cd_to_cwd_git_rewritten_to_plain_git(self):  # C1: the cd is dropped, git stays plain
        wt = self._enter_cwd_worktree()
        self.assertEqual(cmd_guard.rewrite_cd_git(f"cd {wt} && git status --short"), "git status --short")
        self.assertEqual(cmd_guard.rewrite_cd_git(f"cd {wt} && git log && cat f"), "git log && cat f")
        got = self._run(f"cd {wt} && git status --short")
        self.assertEqual(got["hookSpecificOutput"]["updatedInput"]["command"], "git status --short")
        self.assertNotIn("permissionDecision", got["hookSpecificOutput"])

    def test_cd_to_cwd_qrun_not_denied(self):  # C1: a cd into the cwd before a qrun call stays allowed
        wt = self._enter_cwd_worktree()
        self.assertEqual(cmd_guard.verdict(f"cd {wt} && {Q} x"), "")

    def test_cd_elsewhere_still_denied(self):
        wt = self._enter_cwd_worktree()
        other = os.path.dirname(wt)
        self.assertIn("No `cd", cmd_guard.verdict(f"cd {other} && git status"))

    def test_cd_git_denial_text_gives_git_c_not_cd(self):  # C2: the replacement is `git -C D args`, never a cd
        why = cmd_guard.verdict("cd /wt/x && git status --short")
        self.assertIn("Run `git -C /wt/x status --short` instead", why)
        self.assertNotIn("Run `cd", why)

    def _run(self, cmd, extra=None):
        import io
        import json
        from contextlib import redirect_stdout
        from unittest import mock

        data = {"tool_input": {"command": cmd, **(extra or {})}}
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(data))), redirect_stdout(out):
            self.assertEqual(cmd_guard.main(), 0)
        return json.loads(out.getvalue()) if out.getvalue() else {}

    def test_output_shape(self):
        got = self._run("cd /w && git status", {"description": "d", "timeout": 5})["hookSpecificOutput"]
        self.assertEqual(got["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", got)
        self.assertEqual(got["updatedInput"], {"command": "git -C /w status", "description": "d", "timeout": 5})

    def test_dependent_keeps_denial_message(self):
        got = self._run("cd /w && git status && cat f")["hookSpecificOutput"]
        self.assertEqual(got["permissionDecision"], "deny")
        self.assertIn("git -C /w status", got["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
