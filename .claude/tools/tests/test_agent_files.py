"""agent_files: the Strata tool loop, with a scripted fake model (no network)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "localmodel"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import agent_files as af


def scripted(replies):
    seen = []

    def post(msgs, tools=True):
        seen.append((len(msgs), tools))
        return replies[min(len(seen) - 1, len(replies) - 1)]

    post.seen = seen  # type: ignore[attr-defined]  # test helper hangs the call log on the fake
    return post


def tool(i, name, **a):
    return {"content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": a}]}


def final(text):
    return {"content": [{"type": "text", "text": text}]}


class AgentFiles(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        os.makedirs(f"{self.d}/a")
        with open(f"{self.d}/a/x.py", "w") as fh:
            fh.write("one\ntwo\nthree\n")
        self.repos = {"r": self.d}

    def test_tool_turn_then_answer_drops_unknown_repo_and_ghost_files(self):
        post = scripted(
            [
                tool(1, "find", repo="r", query="q"),
                final('{"files": [{"repo": "r", "path": "a/x.py", "why": "w"}, {"repo": "zz", "path": "a/x.py"}, {"repo": "r", "path": "ghost.py"}]}'),
            ]
        )
        log: list = []
        got = af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: "result", log=log)
        self.assertEqual(got, [("r", "a/x.py", "w", "")])
        self.assertEqual(len(log), 1)

    def test_cap_forces_an_answer_without_tools(self):
        post = scripted([tool(1, "find", repo="r", query="q")] * af.MAX_TURNS + [final('{"files": []}')])
        got = af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: "x")
        self.assertEqual(got, [])
        self.assertFalse(post.seen[-1][1])

    def test_consecutive_tool_errors_force_an_answer(self):
        post = scripted([tool(1, "find", repo="r", query="q")] * af.MAX_TOOL_ERRORS + [final('{"files": []}')])
        got = af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: "error: nope")
        self.assertEqual(got, [])
        self.assertEqual(af.STATS["ended"], "tool-errors")
        self.assertEqual(af.STATS["tool_calls"], af.MAX_TOOL_ERRORS)
        self.assertFalse(post.seen[-1][1])

    def test_a_good_result_resets_the_error_run(self):
        res = iter(["error: x", "error: x", "error: x", "ok", "error: x", "error: x", "error: x"])
        post = scripted([tool(i, "find", repo="r", query="q") for i in range(7)] + [final('{"files": []}')])
        af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: next(res))
        self.assertEqual(af.STATS["tool_calls"], 7)

    def test_parallel_turn_with_one_error_keeps_counting(self):
        two = {
            "content": [
                {"type": "tool_use", "id": "a", "name": "find", "input": {"repo": "r", "query": "q"}},
                {"type": "tool_use", "id": "b", "name": "find", "input": {"repo": "r", "query": "q"}},
            ]
        }
        res = iter(["error: x", "ok"] * af.MAX_TOOL_ERRORS * 2)
        post = scripted([two] * af.MAX_TOOL_ERRORS + [final('{"files": []}')])
        af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: next(res))
        self.assertEqual(af.STATS["ended"], "tool-errors")

    def test_time_budget_forces_an_answer_without_tools(self):
        post = scripted([tool(1, "find", repo="r", query="q"), final('{"files": []}')])
        old, af.BUDGET_S = af.BUDGET_S, -1  # already over budget after the first tool turn
        try:
            got = af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: "x")
        finally:
            af.BUDGET_S = old
        self.assertEqual(got, [])
        self.assertFalse(post.seen[-1][1])
        self.assertEqual(len(post.seen), 2)

    def test_stats_record_calls_tools_and_how_the_loop_ended(self):
        post = scripted([tool(1, "find", repo="r", query="q"), final('{"files": []}')])
        af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: "x")
        st = af.STATS
        self.assertEqual((st["calls"], st["tool_calls"], st["turns"], st["ended"]), (2, 1, 2, "answer"))
        af.agent_files(self.repos, "req", post=lambda m, tools=True: (_ for _ in ()).throw(OSError("down")))
        self.assertEqual(af.STATS["ended"], "error:OSError")

    def test_old_tool_results_are_cut_to_their_head(self):
        seen = []

        def post(msgs, tools=True):
            seen.append([m["content"] for m in msgs if m["role"] == "user" and isinstance(m["content"], list)])
            return tool(len(seen), "find", repo="r", query="q") if len(seen) < 9 else final('{"files": []}')

        old, af.KEEP_RESULTS = af.KEEP_RESULTS, 2
        try:
            af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: "BIGRESULT" * 50)
        finally:
            af.KEEP_RESULTS = old
        last = [b["content"] for turn in seen[-1] for b in turn if b.get("type") == "tool_result"]
        self.assertEqual([c.startswith("(cut") for c in last].count(False), 2)
        cut = [c for c in last if c.startswith("(cut")]
        self.assertTrue(cut)
        self.assertTrue(all("find" in c and "BIGRESULT" in c and len(c) < 400 for c in cut))

    def test_ledger_lists_every_file_a_result_mentioned_and_survives_trimming(self):
        with open(f"{self.d}/a/y.py", "w") as fh:
            fh.write('"""Parses the y format."""\nx = 1\n')
        seen = []

        def post(msgs, tools=True):
            seen.append(msgs)
            return tool(len(seen), "find", repo="r", query="q") if len(seen) < 5 else final('{"files": []}')

        results = iter(["hit a/y.py:1", "nothing", "nothing", "nothing"])
        old, af.KEEP_RESULTS = af.KEEP_RESULTS, 1
        try:
            af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: next(results))
        finally:
            af.KEEP_RESULTS = old
        text = " ".join(b.get("text", "") for b in seen[-1][-1]["content"])
        self.assertIn("r:a/y.py - Parses the y format.", text)
        self.assertNotIn("a/x.py", text)
        self.assertEqual(
            sum(
                1 for m in seen[-1] for b in (m["content"] if isinstance(m["content"], list) else []) if b.get("type") == "text" and "FILES FOUND" in b["text"]
            ),
            1,
        )

    def test_with_ledger_does_not_change_the_history(self):
        msgs: list[dict] = [{"role": "user", "content": "head"}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]}]
        out = af.with_ledger(msgs, {("r", "a/x.py"): "d"})
        self.assertEqual(len(msgs[-1]["content"]), 1)
        self.assertEqual(len(out[-1]["content"]), 2)

    def test_failure_and_no_json_give_none(self):
        def boom(msgs, tools=True):
            raise OSError("down")

        self.assertIsNone(af.agent_files(self.repos, "req", post=boom))
        self.assertIsNone(af.agent_files(self.repos, "req", post=scripted([final("no json")])))

    def test_no_json_after_tool_results_falls_back_to_the_ledger(self):
        post = scripted([tool(1, "find", repo="r", query="q"), final(""), final("")])
        got = af.agent_files(self.repos, "req", post=post, run=lambda r, n, a: "hit a/x.py:1")
        self.assertEqual([(r, p) for r, p, *_ in got], [("r", "a/x.py")])
        self.assertEqual(af.STATS["ended"], "ledger")

    def test_early_prose_answer_gets_one_retry_for_the_json(self):
        post = scripted([final("I think it is a/x.py"), final('{"files": [{"repo": "r", "path": "a/x.py", "why": "w"}]}')])
        got = af.agent_files(self.repos, "req", post=post)
        self.assertEqual([(r, p) for r, p, *_ in got], [("r", "a/x.py")])
        self.assertEqual(post.seen[1][1], False)  # the retry runs without tools
        self.assertEqual(af.STATS["ended"], "answer")

    def test_read_is_limited_and_stays_inside_the_repo(self):
        self.assertEqual(af.run_tool(self.repos, "read", {"repo": "r", "file": "a/x.py", "start": 2, "end": 3}), "2: two\n3: three")
        self.assertIn("outside", af.run_tool(self.repos, "read", {"repo": "r", "file": "../etc/passwd", "start": 1, "end": 2}))
        self.assertIn("unknown repo", af.run_tool(self.repos, "find", {"repo": "q", "query": "x"}))
        self.assertIn("error", af.run_tool(self.repos, "read", {"repo": "r", "file": "a/x.py"}))

    def test_read_refuses_symlinks_and_absolute_paths_that_leave_the_repo(self):
        os.symlink("/etc/passwd", f"{self.d}/a/link")
        for f in ("a/link", "/etc/passwd", "a/../../etc/passwd"):
            self.assertEqual(af.run_tool(self.repos, "read", {"repo": "r", "file": f, "start": 1, "end": 2}), "error: path outside the repo", f)

    def test_git_grep_refuses_pager_and_pattern_file_options(self):
        msg = "error: git grep: no -f, --file or -O (reads a file or runs a pager)"
        for args in ("grep -Osh x", "grep --open-files-in-pager=vi x", "grep -f /etc/passwd .", "grep --file=/etc/passwd ."):
            self.assertEqual(af.run_tool(self.repos, "git", {"repo": "r", "args": args}), msg, args)

    def test_parse_files_and_note_files_keep_model_paths_inside_the_repo(self):
        with tempfile.TemporaryDirectory() as out:
            with open(f"{out}/secret.txt", "w") as fh:
                fh.write("s")
            paths = [f"{self.d}/a/x.py", f"{out}/secret.txt", "../secret.txt"]
            text = json.dumps({"code": [{"repo": "r", "path": p} for p in paths]})
            self.assertEqual(af.parse_files(text, self.repos), [("r", "a/x.py", "", "code")])
            ledger: dict = {}
            af.note_files(ledger, self.repos, f"see {out}/secret.txt, ../secret.txt and a/x.py")
            self.assertEqual(list(ledger), [("r", "a/x.py")])


class Categories(unittest.TestCase):
    def test_why_lines_are_grouped_by_category(self):
        from localmodel import plan_context as pc

        with tempfile.TemporaryDirectory() as d:
            for n in ("a.py", "t.py"):
                with open(os.path.join(d, n), "w") as fh:
                    fh.write("x")
            text = "\n".join(pc._explore_one(d, ["a.py", "t.py"], [("a.py", "does a", "code"), ("t.py", "tests a", "tests")]))
            self.assertLess(text.index("### code"), text.index("- a.py: does a"))
            self.assertLess(text.index("- a.py: does a"), text.index("### tests"))
            self.assertLess(text.index("### tests"), text.index("- t.py: tests a"))

    def test_parse_categories(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("a.py", "t.py"):
                with open(os.path.join(d, n), "w") as fh:
                    fh.write("x")
            text = '{"code": [{"repo": "r", "path": "a.py", "why": "w"}], "tests": [{"repo": "r", "path": "t.py"}], "docs": [], "data": []}'
            self.assertEqual(af.parse_files(text, {"r": d}), [("r", "a.py", "w", "code"), ("r", "t.py", "", "tests")])

    def test_bash_only_inside_boxes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(af.run_tool({"r": d}, "bash", {"repo": "r", "cmd": "ls"}), "error: no shell in this run")
            af.BOXES["r"] = d
            try:
                import safeshell

                allowed = ", ".join(sorted(safeshell.ALLOW))
                self.assertEqual(af.run_tool({"r": d}, "bash", {"repo": "r", "cmd": "rm -rf ."}), f"error: `rm` is not allowed; allowed: {allowed}")
                self.assertEqual(af.run_tool({"r": d}, "bash", {"repo": "r", "cmd": "ls"}), "(no result)")
            finally:
                af.BOXES.clear()


if __name__ == "__main__":
    unittest.main()


class TruncatedAnswerTest(unittest.TestCase):
    def test_cut_off_json_keeps_complete_entries(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("a.md", "b.md"):
                open(os.path.join(d, n), "w").close()
            text = '{"files": [{"repo": "r", "path": "a.md", "why": "x"}, {"repo": "r", "path": "b.md", "wh'
            self.assertEqual(af.parse_files(text, {"r": d}), [("r", "a.md", "x", "")])
            self.assertIsNone(af.parse_files("no json here", {"r": d}))


class PostBodyTest(unittest.TestCase):
    def test_post_sends_the_messages_list_not_a_function(self):
        import unittest.mock as um

        import search.strata as strata

        sent = []
        with um.patch.object(strata, "messages", lambda body, timeout: sent.append(body) or {"content": []}):
            af._post([{"role": "user", "content": "q"}])
        self.assertEqual(sent[0]["messages"], [{"role": "user", "content": "q"}])
        json.dumps(sent[0])
