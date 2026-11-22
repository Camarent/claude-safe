import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, cli_wave, driver
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp


def res(text="VERDICT: DONE", cost=0.5):
    return {
        "result": text,
        "num_turns": 3,
        "duration_ms": 2000,
        "total_cost_usd": cost,
        "is_error": False,
        "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40},
    }


class WaveTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.w = f"{self.d}/.work/s"
        os.makedirs(self.w)
        self.script, self.reports, self.claude = [], [], []
        self._o, self._i, self._c = driver.orch, driver.orch_inproc, claude.run_claude
        driver.orch, driver.orch_inproc, claude.run_claude = self.fake_orch, self.fake_orch, self.fake_claude
        self.handler = lambda args: res()

    def tearDown(self):
        driver.orch, driver.orch_inproc, claude.run_claude = self._o, self._i, self._c

    def fake_orch(self, cmd, stage, dir, *extra):
        if cmd == "next":
            return self.script.pop(0)
        self.reports.append(extra)
        return {"recorded": extra[1], "verdict": "DONE"}

    def fake_claude(self, args, cwd, timeout, done_file=None, env_extra=None, pgid_file=None):
        self.claude.append(args)
        return self.handler(args)

    def run_w(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_wave.main(["run", "--stage", "s", "--dir", self.d, *extra])
        return rc, out.getvalue()

    def disp(self, *ids, model="haiku"):
        return {
            "action": "dispatch",
            "dispatches": [{"id": i, "task": f"task-{i}", "role": "dev", "agent": "dev", "model": model, "prompt": f"do {i}"} for i in ids],
        }

    def metrics(self):
        with open(f"{self.w}/metrics.jsonl", encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_metrics_and_total(self):
        self.script = [self.disp("a1", "a2"), {"action": "done", "message": "ok"}]
        rc, out = self.run_w()
        self.assertEqual(rc, 0)
        self.assertIn("done: ok", out)
        m = self.metrics()
        ss = [r for r in m if r["ev"] == "session"]
        self.assertEqual(len(ss), 2)
        self.assertEqual(
            {k: ss[0][k] for k in ("turns", "input", "output", "cache_read", "cache_write", "cost_usd", "seconds", "verdict")},
            {"turns": 3, "input": 10, "output": 20, "cache_read": 30, "cache_write": 40, "cost_usd": 0.5, "seconds": 2.0, "verdict": "DONE"},
        )
        wv = [r for r in m if r["ev"] == "wave"][0]
        self.assertEqual((wv["sessions"], wv["cost_usd"], wv["input"]), (2, 1.0, 20))
        self.assertEqual(m[-1]["ev"], "total")
        self.assertEqual(m[-1]["sessions"], 2)

    def test_parallel_and_prompt(self):
        barrier = threading.Barrier(2, timeout=5)

        def h(args):
            barrier.wait()
            return res()

        self.handler = h
        self.script = [self.disp("a1", "a2"), {"action": "done", "message": ""}]
        self.assertEqual(self.run_w()[0], 0)
        a = self.claude[0]
        self.assertIn("--agent", a)
        self.assertNotIn("State (read first)", a[-1])
        self.assertTrue(a[-1].endswith("do a1") or a[-1].endswith("do a2"))

    def test_report_fallback(self):
        os.makedirs(f"{self.w}/reports", exist_ok=True)
        Path(f"{self.w}/reports/report-a2.md").write_text("mine", encoding="utf-8")
        self.handler = lambda args: res("from result")
        self.script = [self.disp("a1", "a2"), {"action": "done", "message": ""}]
        self.run_w()
        self.assertEqual(Path(f"{self.w}/reports/report-a1.md").read_text(encoding="utf-8"), "from result")
        self.assertEqual(Path(f"{self.w}/reports/report-a2.md").read_text(encoding="utf-8"), "mine")

    def test_block_returns_to_lead_without_model_session(self):
        self.script = [{"action": "ask_main", "message": "a blocked\nb blocked"}]
        self.handler = lambda args: self.fail("no claude session on a block")
        rc, out = self.run_w()
        self.assertEqual(rc, 4)
        self.assertIn("a blocked", out)
        self.assertIn("b blocked", Path(f"{self.w}/BLOCKED.md").read_text(encoding="utf-8"))
        self.assertFalse(os.path.exists(f"{self.w}/wave-ladder.json"))

    def test_ask_text_has_cause_tail_and_command_and_counts_repeats(self):
        rep = f"{self.d}/report-merge.md"
        Path(rep).write_text("line one\nFAIL: conflict in a.py\n", encoding="utf-8")
        msg = (
            f"merge-w1: FAIL but no conflict in {rep}, so no merger is dispatched: read it, then `orch.py retry --step merge-w1`.\n"
            f"merge-w2: FAIL but no conflict in {self.d}/other.md, so no merger is dispatched: read it.\n"
            f"merge-w1: FAIL but no conflict in {rep}, so no merger is dispatched: read it, then `orch.py retry --step merge-w1`."
        )
        text = cli_wave._ask_text(type("A", (), {"stage": "s"})(), msg)
        self.assertIn(f"merge-w1: FAIL but no conflict in {rep}, so no merger is dispatched: read it, then `orch.py retry --step merge-w1`. (x2)", text)
        self.assertEqual(text.count("merge-w1:"), 1)
        self.assertIn("FAIL: conflict in a.py", text)  # error tail of the named report
        self.assertIn("suggested: `orch.py retry --step merge-w1`", text)
        self.assertIn("suggested: `", text.split("merge-w2:")[1])  # no retry command named: a default one is suggested
        self.assertIn("error tail: none, the ask names no report file", text)

    def test_review_failed_ask_shows_findings_and_note_hint(self):
        rep = f"{self.d}/report-review.md"
        lines = ["VERDICT: FAIL"] + [f"W{i}: fix text {i}" for i in range(1, 11)]
        Path(rep).write_text("\n".join(lines) + "\n", encoding="utf-8")
        msg = f"task-1: review failed 2 rounds; findings {rep}; fix the cause, then `orch.py retry --stage s --step task-1` (or `skip`)"
        text = cli_wave._ask_text(type("A", (), {"stage": "s"})(), msg)
        self.assertIn("W1: fix text 1", text)  # beyond the 5-line tail
        note_cmd = '--step task-1 --note "<quote the reviewer\'s exact fix text>"'
        self.assertIn(note_cmd, text)

    def test_start_clears_stale_blocked_file(self):
        Path(f"{self.w}/BLOCKED.md").write_text("old block\n", encoding="utf-8")
        self.script = [self.disp("a1", "a2"), {"action": "done", "message": "ok"}]
        self.assertEqual(self.run_w()[0], 0)
        self.assertFalse(os.path.exists(f"{self.w}/BLOCKED.md"))

    def test_live_lock_blocks_the_run(self):
        self.assertTrue(cli_wave._take_lock(f"{self.w}/wave.lock"))  # another driver holds it
        self.addCleanup(cli_wave._release_lock, f"{self.w}/wave.lock")
        self.script = [{"action": "done", "message": "ok"}]
        rc, out = self.run_w()
        self.assertEqual(rc, 4)
        self.assertIn("already runs", out)

    def test_live_lock_means_no_review_recover(self):  # review audit H2: a second driver must not reset the first one's job worktrees
        self.assertTrue(cli_wave._take_lock(f"{self.w}/wave.lock"))  # another driver holds it
        self.addCleanup(cli_wave._release_lock, f"{self.w}/wave.lock")
        self.script = [{"action": "done", "message": "ok"}]
        with mock.patch.object(cli_wave, "review_recover", return_value=[]) as rec:
            rc, _out = self.run_w()
        self.assertEqual(rc, 4)
        rec.assert_not_called()

    def test_checkout_lock_of_another_stage_blocks_the_run(self):  # two stages, one main checkout: the second driver is refused
        self.assertTrue(cli_wave._take_lock(f"{self.d}/.work/checkout.lock"))  # the first stage holds the checkout
        self.addCleanup(cli_wave._release_lock, f"{self.d}/.work/checkout.lock")
        self.script = [{"action": "done", "message": "ok"}]
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_wave.main(["run", "--stage", "t", "--dir", self.d])
        self.assertEqual(rc, 4)
        self.assertIn(f"blocked: another stage runs in {self.d}", out.getvalue())
        self.assertFalse(os.path.exists(f"{self.d}/.work/t/wave.lock"))  # the stage lock taken first is given back
        self.assertTrue(os.path.exists(f"{self.d}/.work/checkout.lock"))  # the other stage's lock stays

    def test_checkout_lock_is_released_after_the_run(self):
        self.script = [{"action": "done", "message": "ok"}]
        rc, _out = self.run_w()
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(f"{self.d}/.work/checkout.lock"))

    def test_dead_lock_is_taken_over(self):
        Path(f"{self.w}/wave.lock").write_text("2999999", encoding="utf-8")
        self.script = [{"action": "done", "message": "ok"}]
        self.assertEqual(self.run_w()[0], 0)

    def test_lock_is_taken_before_the_guards_and_released_after_a_block(self):
        seen = []

        def guard(a):
            seen.append(os.path.exists(f"{self.w}/wave.lock"))
            return "dirty tree" if len(seen) == 1 else None

        with mock.patch.object(cli_wave, "dirty_block", side_effect=guard):
            rc, out = self.run_w()
        self.assertEqual((rc, seen), (4, [True]))
        self.assertIn("dirty tree", out)
        self.assertFalse(os.path.exists(f"{self.w}/wave.lock"))

    def test_lock_is_released_when_setup_raises(self):
        with mock.patch.object(cli_wave, "recover_unreported", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            self.run_w()
        self.assertFalse(os.path.exists(f"{self.w}/wave.lock"))

    def test_lock_is_taken_once_and_leaves_no_temp_files(self):
        lock = f"{self.w}/wave.lock"
        self.assertTrue(cli_wave._take_lock(lock))
        self.assertEqual(Path(lock).read_text(encoding="utf-8").strip(), str(os.getpid()))
        self.assertFalse(cli_wave._take_lock(lock))  # held: refused
        cli_wave._release_lock(lock)
        self.assertFalse(os.path.exists(lock))
        self.assertTrue(cli_wave._take_lock(lock))  # free again
        cli_wave._release_lock(lock)
        self.assertEqual(sorted(n for n in os.listdir(self.w) if n.startswith("wave.lock")), [])

    def test_release_of_a_lock_not_held_leaves_it_alone(self):
        lock = f"{self.w}/wave.lock"
        Path(lock).write_text("2999999", encoding="utf-8")
        cli_wave._release_lock(lock)
        self.assertTrue(os.path.exists(lock))

    def test_lock_of_a_killed_driver_is_free(self):  # the kernel drops the flock with the process: no stale check needed
        lock = f"{self.w}/wave.lock"
        code = f"import os,sys; sys.path.insert(0, {str(Path(cli_wave.__file__).parents[1])!r}); from pipeline import cli_wave; assert cli_wave._take_lock({lock!r}); os._exit(0)"
        subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
        self.assertTrue(os.path.exists(lock))  # left behind, held by nobody
        self.assertTrue(cli_wave._take_lock(lock))
        cli_wave._release_lock(lock)

    def test_takeover_by_four_drivers_gives_one_winner(self):
        lock = f"{self.w}/wave.lock"
        Path(lock).write_text("2999999", encoding="utf-8")
        got = []
        barrier = threading.Barrier(4)

        def go():
            barrier.wait()
            got.append(cli_wave._take_lock(lock))

        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(got.count(True), 1)
        cli_wave._release_lock(lock)
        self.assertEqual(sorted(n for n in os.listdir(self.w) if n.startswith("wave.lock")), [])

    def test_limits(self):
        def heavy(*ids):
            return self.disp(*ids, model="sonnet")

        self.script = [heavy("a1"), heavy("a2"), heavy("a3")]
        rc, _out = self.run_w("--max-waves", "2")
        self.assertEqual(rc, 3)
        self.assertEqual(len(self.reports), 2)
        self.script = [heavy("b1", "b2")]
        self.assertEqual(self.run_w("--max-dispatches", "1")[0], 3)
        self.script = [self.disp("c1"), self.disp("c2")]
        self.assertEqual(self.run_w("--max-budget-usd", "0.1")[0], 3)

    def test_only_sonnet_and_opus_count_toward_the_limits(self):
        def scr(i):
            return {
                "action": "dispatch",
                "dispatches": [
                    {"id": i, "task": f"s-{i}", "role": "script", "agent": "script", "model": None, "prompt": "python3 -I /root/.claude/tools/qrun.py -- true"}
                ],
            }

        done = {"action": "done", "message": "ok"}
        self.script = [scr("s1"), self.disp("h1", "h2"), self.disp("h3"), self.disp("h4"), done]  # scripts and Haiku are free
        self.assertEqual(self.run_w("--max-waves", "1", "--max-dispatches", "1")[0], 0)
        self.script = [self.disp("g1", model="sonnet"), self.disp("g2", model="opus"), done]  # a second heavy wave hits --max-waves 1
        self.assertEqual(self.run_w("--max-waves", "1")[0], 3)

        def sec(i):
            return {"action": "dispatch", "dispatches": [{"id": i, "task": "t", "role": "sec", "agent": "sec-auditor", "model": None, "prompt": "p"}]}

        self.script = [sec("x1"), sec("x2"), done]
        self.assertEqual(self.run_w("--max-dispatches", "1")[0], 3)  # model None: the agent file's default (opus) counts
        self.script = [self.disp("l1"), self.disp("l2"), done]  # the loop guard counts every model session
        self.assertEqual(self.run_w("--max-sessions", "1")[0], 3)

    def test_status_closes_merge_steps_that_the_retry_merge_replaced(self):
        log = [
            {"ev": "dispatch", "id": "a", "task": "merge-w2", "role": "script"},
            {"ev": "report", "id": "a", "task": "merge-w2", "role": "script", "verdict": "FAIL"},
            {"ev": "dispatch", "id": "b", "task": "merge-fix-w2", "role": "dev"},
            {"ev": "dispatch", "id": "c", "task": "merge-w2-2", "role": "script"},
            {"ev": "report", "id": "c", "task": "merge-w2-2", "role": "script", "verdict": "DONE"},
        ]
        Path(f"{self.w}/orch.jsonl").write_text("".join(json.dumps(e) + "\n" for e in log), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            cli_wave.main(["status", "--stage", "s", "--dir", self.d])
        self.assertEqual(out.getvalue().split("\n")[:3], ["merge-w2 done", "merge-fix-w2 done", "merge-w2-2 done"])

    def test_status_and_help(self):
        log = [
            {"ev": "dispatch", "id": "a", "task": "task-1", "role": "reviewer"},
            {"ev": "report", "id": "a", "task": "task-1", "role": "reviewer", "verdict": "PASS"},
            {"ev": "dispatch", "id": "b", "task": "task-2", "role": "reviewer"},
            {"ev": "report", "id": "b", "task": "task-2", "role": "reviewer", "verdict": "FAIL"},
            {"ev": "report", "id": "b", "task": "task-2", "role": "reviewer", "verdict": "FAIL"},
            {"ev": "dispatch", "id": "c", "task": "task-3", "role": "dev"},
            {"ev": "dispatch", "id": "d", "task": "task-4", "role": "dev"},
        ]
        Path(f"{self.w}/orch.jsonl").write_text("".join(json.dumps(e) + "\n" for e in log), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            cli_wave.main(["status", "--stage", "s", "--dir", self.d])
        expected = ["task-1 done", "task-2 failed", "task-3 open", "task-4 open"]
        self.assertEqual(out.getvalue().split("\n")[:4], expected)

    def test_project_allow_from_pipeline_json(self):
        with mock.patch.object(claude, "project", return_value={"allow": ["Bash(x validate *)"]}):
            args = claude.claude_args("dev", None, None, "p")
        self.assertEqual(args[args.index("Bash(x validate *)") - 1], "--allowedTools")
        with mock.patch.object(claude, "project", return_value={}):
            self.assertEqual(claude.project_allow(), [])

    def test_ops_only_rm(self):
        self.assertIn(claude.OPS_FLAGS[0], claude.claude_args("ops", None, None, "p"))
        self.assertFalse(any("rm " in a for a in claude.claude_args("dev", None, None, "p")))

    def test_usage_tools_from_transcript(self):
        import json as j

        h = tracked_mkdtemp()
        d = os.path.join(h, ".claude", "projects", "x")
        os.makedirs(d)
        with open(os.path.join(d, "sid1.jsonl"), "w") as f:
            for n in ("Read", "Read", "Grep"):
                f.write(j.dumps({"message": {"content": [{"type": "tool_use", "name": n}]}}) + "\n")
        old = os.environ.get("HOME")
        os.environ["HOME"] = h
        try:
            self.assertEqual(claude.usage({"session_id": "sid1"})["tools"], {"Read": 2, "Grep": 1})
        finally:
            os.environ["HOME"] = old  # type: ignore[assignment]  # HOME was set by the test host

    def test_claude_args_without_model(self):
        self.assertNotIn("--model", claude.claude_args("security", None, None, "p"))
        self.assertIn("--model", claude.claude_args("dev", "sonnet", None, "p"))


class DryRunTest(unittest.TestCase):
    def test_dry_run_walks_on_in_a_copy(self):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/s")
        with open(f"{d}/.work/s/TASKS.md", "w", encoding="utf-8") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n## Graph\nwave 1: task-1\nflags: \n"
            )
        Path(f"{d}/AUDIT.md").write_text('```audit-tools\nv | . | printf "" | vulture\n```\n', encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_wave.main(["run", "--stage", "s", "--dir", d, "--dry-run"])
        o = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertGreater(len(o.strip().splitlines()), 1)  # mechanism only: it walks past the first dispatch; which steps exist is not pinned
        self.assertEqual(sorted(os.listdir(f"{d}/.work/s")), ["TASKS.md"])  # the real stage dir is untouched

    def test_dry_run_leaves_no_scratch_dir(self):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/s")
        Path(f"{d}/.work/s/TASKS.md").write_text("| task | what | owner | price | review |\n|---|---|---|---|---|\n", encoding="utf-8")
        scratch = tracked_mkdtemp()
        with mock.patch.object(tempfile, "tempdir", scratch), redirect_stdout(io.StringIO()):
            cli_wave.main(["run", "--stage", "s", "--dir", d, "--dry-run"])
        self.assertEqual(os.listdir(scratch), [])  # the wave-dry-* copy is removed when the walk ends

    def test_dry_run_missing_stage_creates_no_scratch_dir(self):
        d = tracked_mkdtemp()
        scratch = tracked_mkdtemp()
        with mock.patch.object(tempfile, "tempdir", scratch), redirect_stdout(io.StringIO()):
            self.assertEqual(cli_wave.main(["run", "--stage", "nope", "--dir", d, "--dry-run"]), 1)
        self.assertEqual(os.listdir(scratch), [])


if __name__ == "__main__":
    unittest.main()
