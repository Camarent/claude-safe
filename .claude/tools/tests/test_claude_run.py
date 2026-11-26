"""pipeline/claude.py: run_claude, session and usage against fake `claude` executables on a temp PATH (no model, no network)."""

import os
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import UTC, datetime

from pipeline import claude
from tests.support.tmpdirs import tracked_mkdtemp


class FakeClaude(unittest.TestCase):
    def setUp(self):
        self.bin = tracked_mkdtemp()
        self.cwd = tracked_mkdtemp()
        patcher = mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("POLL_S", 0.1), ("REPORT_GRACE_S", 0.2)):
            p = mock.patch.object(claude, name, value)
            p.start()
            self.addCleanup(p.stop)

    def fake(self, body):
        path = os.path.join(self.bin, "claude")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class RunClaudeTest(FakeClaude):
    def test_json_output_is_returned_parsed(self):
        self.fake("""printf '{"result":"hi","num_turns":3,"total_cost_usd":0.1,"duration_ms":2500,"session_id":"none-such","usage":{"input_tokens":7}}'""")
        res = claude.run_claude(["-p", "x"], self.cwd, 5)
        self.assertEqual((res["result"], res["num_turns"]), ("hi", 3))

    def test_pipeline_token_becomes_the_oauth_token_except_for_the_publisher(self):
        self.fake('printf \'{"result":"%s"}\' "$CLAUDE_CODE_OAUTH_TOKEN"')
        with mock.patch.dict(os.environ, {"PIPELINE_CLAUDE_TOKEN": "tok-b", "CLAUDE_CODE_OAUTH_TOKEN": ""}):
            self.assertEqual(claude.run_claude(["-p", "x"], self.cwd, 5)["result"], "tok-b")
            for name in ("publisher", "pl-publisher"):
                self.assertEqual(claude.run_claude(["--agent", name, "x"], self.cwd, 5)["result"], "")

    def test_only_the_publisher_session_gets_the_artifact_env(self):
        self.fake('printf \'{"result":"%s"}\' "$CLAUDE_CODE_ARTIFACT"')
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_ARTIFACT": ""}):
            self.assertEqual(claude.run_claude(["--agent", "publisher", "x"], self.cwd, 5)["result"], "1")
            self.assertEqual(claude.run_claude(["--agent", "pl-publisher", "x"], self.cwd, 5)["result"], "1")
            self.assertEqual(claude.run_claude(["-p", "x"], self.cwd, 5)["result"], "")
            self.assertEqual(claude.run_claude(["--agent", "dev", "x"], self.cwd, 5)["result"], "")

    def test_only_the_publisher_gets_the_artifact_allow_flag_before_the_prompt(self):
        self.fake('printf \'{"result":"%s"}\' "$*"')
        pub = claude.run_claude(["-p", "--agent", "publisher", "--", "x"], self.cwd, 5)["result"]
        self.assertIn("--allowedTools Artifact --", pub)
        self.assertTrue(pub.endswith(" -- x"), pub)
        for args in (["--agent", "dev", "--", "x"], ["-p", "--", "x"]):
            self.assertNotIn("Artifact", claude.run_claude(args, self.cwd, 5)["result"])
        self.assertEqual(claude.artifact_allow(["--agent", "publisher", "x"]), ["--agent", "publisher", "--allowedTools", "Artifact", "x"])

    def test_a_crash_without_json_reports_the_return_code(self):
        self.fake("echo boom >&2; exit 3")
        res = claude.run_claude([], self.cwd, 5)
        self.assertTrue(res["is_error"])
        self.assertIn("rc=3", res["result"])
        self.assertIn("boom", res["result"])

    def test_a_session_over_the_timeout_is_killed(self):
        self.fake("exec sleep 30")
        res = claude.run_claude([], self.cwd, 0.3)
        self.assertEqual(res, {"is_error": True, "result": "wave: session timeout"})

    def test_a_hung_session_is_closed_after_its_report_has_a_verdict(self):
        report = os.path.join(self.cwd, "report.md")
        with open(report, "w", encoding="utf-8") as f:
            f.write("VERDICT: DONE\nbody\n")
        self.fake("exec sleep 30")
        res = claude.run_claude([], self.cwd, 20, done_file=report)
        self.assertFalse(res["is_error"])
        self.assertIn("hung session closed", res["result"])

    def test_a_report_without_a_verdict_does_not_close_the_session(self):
        report = os.path.join(self.cwd, "report.md")
        with open(report, "w", encoding="utf-8") as f:
            f.write("still thinking\n")
        self.fake("exec sleep 30")
        res = claude.run_claude([], self.cwd, 0.6, done_file=report)
        self.assertIn("timeout", res["result"])

    def test_a_missing_claude_binary_is_an_error_not_a_crash(self):
        with mock.patch.dict(os.environ, {"PATH": self.bin}):
            res = claude.run_claude([], self.cwd, 5)
        self.assertTrue(res["is_error"])
        self.assertIn("claude call failed", res["result"])


class UsageTest(FakeClaude):
    def test_session_maps_the_json_to_a_usage_dict_with_the_session_id(self):
        self.fake(
            """printf '{"result":"done","num_turns":4,"total_cost_usd":0.2,"duration_ms":1500,"session_id":"abc","is_error":false,"usage":{"input_tokens":10,"output_tokens":5,"cache_read_input_tokens":3,"cache_creation_input_tokens":2}}'"""
        )
        u = claude.session([], self.cwd, timeout=5)
        self.assertEqual((u["turns"], u["input"], u["output"], u["cache_read"], u["cache_write"]), (4, 10, 5, 3, 2))
        self.assertEqual((u["cost_usd"], u["seconds"], u["sid"], u["error"], u["result"]), (0.2, 1.5, "abc", False, "done"))

    def test_usage_defaults_for_an_empty_result(self):
        u = claude.usage({})
        self.assertEqual((u["turns"], u["cost_usd"], u["sid"], u["result"], u["error"]), (0, 0, "", "", False))

    def test_denials_become_short_lines(self):
        u = claude.usage(
            {
                "permission_denials": [
                    {"tool_name": "Bash", "tool_input": {"command": "python3 x.py " + "y" * 200}},
                    "junk",
                    {"tool_name": "Write", "tool_input": {"file_path": "/a/b"}},
                ]
            }
        )
        self.assertEqual(u["denials"], ["Bash: python3 x.py " + "y" * 200, "Write: /a/b"])
        long = claude.usage({"permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "z" * 900}}]})
        self.assertEqual(len(long["denials"][0]), len("Bash: ") + 600)
        self.assertEqual(claude.usage({})["denials"], [])


class SmallHelpersTest(unittest.TestCase):
    def test_report_done_needs_a_verdict_first_line(self):
        d = tracked_mkdtemp()
        ok, bad = os.path.join(d, "ok.md"), os.path.join(d, "bad.md")
        with open(ok, "w", encoding="utf-8") as f:
            f.write("VERDICT: PASS\n")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("notes\nVERDICT: PASS\n")
        self.assertTrue(claude.report_done(ok))
        self.assertFalse(claude.report_done(bad))
        self.assertFalse(claude.report_done(os.path.join(d, "missing.md")))

    def test_default_timeout_falls_back_on_garbage_in_the_env(self):
        with mock.patch.dict(os.environ, {claude.ENV_TIMEOUT: "soon"}):
            self.assertEqual(claude.default_timeout(), claude.SESSION_TIMEOUT_S)
        with mock.patch.dict(os.environ, {claude.ENV_TIMEOUT: "12.5"}):
            self.assertEqual(claude.default_timeout(), 12.5)


class AddDirTest(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        env = mock.patch.dict(os.environ, {claude.ENV_PROJECT_ROOT: self.root})
        env.start()
        self.addCleanup(env.stop)

    def test_read_only_roles_get_the_project_dirs_dev_and_reviewer_only_the_scratch_dir(self):
        for agent in claude.ADD_DIR_AGENTS:
            self.assertEqual(claude.add_dir_allow(["--agent", agent, "--", "x"]), ["--agent", agent, "--add-dir", self.root, "--", "x"])
            self.assertEqual(claude.add_dir_allow(["--agent", agent, "x"]), ["--agent", agent, "--add-dir", self.root, "x"])
        for agent in claude.SCRATCH_ADD_AGENTS:
            self.assertEqual(claude.add_dir_allow(["--agent", agent, "--", "x"]), ["--agent", agent, "--add-dir", claude.SCRATCH_ADD_DIR, "--", "x"])
        for agent in ("merger", "ops", "publisher", "web-tester", "ux-reviewer", "docs-writer"):
            for args in (["--agent", agent, "--", "x"], ["--agent", agent, "x"]):
                self.assertEqual(claude.add_dir_allow(args), args)

    def test_pipeline_json_add_dirs_overrides_the_root(self):
        with open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8") as f:
            f.write('{"add_dirs": ["/a", "/b"]}')
        self.assertEqual(claude.add_dir_allow(["--agent", "scout", "--", "x"]), ["--agent", "scout", "--add-dir", "/a", "--add-dir", "/b", "--", "x"])


if __name__ == "__main__":
    unittest.main()


class LimitWaitTest(unittest.TestCase):
    NOTICE = "You've hit your 5-hour limit \u00b7 resets 8pm (UTC)"

    def at(self, hh):
        from datetime import datetime

        return datetime(2026, 11, 6, hh, 0, tzinfo=UTC).timestamp()

    def test_wait_until_the_reset_time_plus_margin(self):
        w = claude.limit_wait_s({"result": self.NOTICE}, now=self.at(19))
        self.assertEqual(w, 3600 + claude.LIMIT_MARGIN_S)

    def test_a_passed_reset_time_means_tomorrow(self):
        w = claude.limit_wait_s({"result": self.NOTICE}, now=self.at(21))
        self.assertEqual(w, 23 * 3600 + claude.LIMIT_MARGIN_S)

    def test_unparsable_reset_time_or_zone_uses_the_fallback(self):
        self.assertEqual(claude.limit_wait_s({"result": "You've hit your usage limit"}), claude.LIMIT_FALLBACK_S)
        self.assertEqual(claude.limit_wait_s({"result": "You've hit your 5-hour limit \u00b7 resets 8pm (Nowhere/Land)"}), claude.LIMIT_FALLBACK_S)

    def test_normal_and_long_text_is_not_a_limit(self):
        self.assertIsNone(claude.limit_wait_s({"result": "done"}))
        self.assertIsNone(claude.limit_wait_s({"result": ""}))
        self.assertIsNone(claude.limit_wait_s({"result": "we hit your usage limit logic here. " * 20}))

    def test_session_waits_then_runs_again_without_a_retry(self):
        results = [{"result": self.NOTICE, "is_error": True}, {"result": "ok", "num_turns": 2}]
        clock = FakeClock()
        with (
            mock.patch.object(claude, "run_claude", side_effect=results) as run,
            mock.patch.object(claude, "limit_wait", side_effect=[claude.LimitWait(5.0, True), None]),
            mock.patch.object(claude.time, "sleep", side_effect=AssertionError("real sleep")),
        ):
            u = claude.session(["-p", "x"], ".", 5, sleep=clock.sleep, now=clock.now)
        self.assertEqual((u["result"], u["turns"]), ("ok", 2))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(clock.slices, [5.0])

    def test_waiting_stops_at_the_cap_and_returns_the_notice(self):
        clock = FakeClock()
        with (
            mock.patch.object(claude, "run_claude", return_value={"result": self.NOTICE, "is_error": True}),
            mock.patch.object(claude, "limit_wait", return_value=claude.LimitWait(float(claude.LIMIT_WAIT_MAX_S) + 1, True)),
            mock.patch.object(claude.time, "sleep", side_effect=AssertionError("real sleep")),
        ):
            u = claude.session(["-p", "x"], ".", 5, sleep=clock.sleep, now=clock.now)
        self.assertEqual(u["result"], self.NOTICE)
        self.assertEqual(clock.slices, [])

    def test_known_reset_waits_in_recheck_slices_until_the_reset_time(self):
        results = [{"result": self.NOTICE, "is_error": True}, {"result": "ok", "num_turns": 1}]
        clock = FakeClock()
        recheck = 900
        with (
            mock.patch.object(claude, "run_claude", side_effect=results),
            mock.patch.object(claude, "limit_wait", side_effect=[claude.LimitWait(2000.0, True), None]),
            mock.patch.object(claude, "tool_setting", return_value=recheck),
            mock.patch.object(claude.time, "sleep", side_effect=AssertionError("real sleep")),
        ):
            u = claude.session(["-p", "x"], ".", 5, sleep=clock.sleep, now=clock.now)
        self.assertEqual(u["result"], "ok")
        self.assertEqual(clock.slices, [900.0, 900.0, 200.0])  # the whole wait is never one sleep
        self.assertEqual(clock.t, 2000.0)

    def test_unknown_reset_sleeps_one_fallback_slice_then_runs_again(self):
        results = [{"result": self.NOTICE, "is_error": True}, {"result": "ok", "num_turns": 1}]
        clock = FakeClock()
        with (
            mock.patch.object(claude, "run_claude", side_effect=results) as run,
            mock.patch.object(claude, "limit_wait", side_effect=[claude.LimitWait(claude.LIMIT_FALLBACK_S, False), None]),
            mock.patch.object(claude, "tool_setting", return_value=300),
            mock.patch.object(claude.time, "sleep", side_effect=AssertionError("real sleep")),
        ):
            u = claude.session(["-p", "x"], ".", 5, sleep=clock.sleep, now=clock.now)
        self.assertEqual(u["result"], "ok")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(clock.slices, [300.0])


class FakeClock:
    """Fake wall clock: sleep advances it, so the wait loop sees time pass without a real sleep."""

    def __init__(self):
        self.t = 0.0
        self.slices = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slices.append(s)
        self.t += s


class LimitWaitFormsTest(unittest.TestCase):
    NOW = datetime(2026, 11, 26, 19, 0, tzinfo=UTC).timestamp()

    def wait(self, text):
        return claude.limit_wait({"result": f"You've hit your 5-hour limit · {text}"}, now=self.NOW)

    def test_each_reset_form_gives_the_wait_until_it(self):
        cases = {
            "resets in 45 minutes": 45 * 60,
            "resets in 2 hours 10 minutes": 2 * 3600 + 600,
            "resets 20:00 (UTC)": 3600,
            "resets 8pm (UTC)": 3600,
            "resets Nov 27, 8pm (UTC)": 25 * 3600,
            "resets Nov 27 at 8:00pm (UTC)": 25 * 3600,
        }
        for text, secs in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.wait(text), claude.LimitWait(secs + claude.LIMIT_MARGIN_S, True))

    def test_a_month_day_already_passed_this_year_means_next_year(self):
        w = self.wait("resets Nov 6, 8pm (UTC)")
        want = datetime(2027, 11, 6, 20, tzinfo=UTC).timestamp() - self.NOW
        self.assertEqual(w, claude.LimitWait(want + claude.LIMIT_MARGIN_S, True))

    def test_impossible_date_or_hour_is_an_unknown_wait(self):
        self.assertEqual(self.wait("resets Feb 31, 8pm (UTC)"), claude.LimitWait(claude.LIMIT_FALLBACK_S, False))
        self.assertEqual(self.wait("resets 25:00 (UTC)"), claude.LimitWait(claude.LIMIT_FALLBACK_S, False))
