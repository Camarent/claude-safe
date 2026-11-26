"""local.py: server-free paths (dispatch, helpers, judge/review with a stubbed model, usage log, daemon client over a socketpair)."""

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import askmodel
from search import ask_code, local


def run(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue(), err.getvalue()


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_text_arg_and_opt(self):
        p = Path(self.tmp, "c.txt")
        p.write_text("criteria", encoding="utf-8")
        self.assertEqual(local.text_arg("@" + str(p)), "criteria")
        self.assertEqual(local.text_arg("plain"), "plain")
        self.assertEqual(local.opt(["--a", "1"], "--a"), "1")
        self.assertEqual(local.opt(["x"], "--a", "d"), "d")
        self.assertIsNone(local.opt([], "--a"))

    def test_script_runs_python(self):
        s = Path(self.tmp, "s.py")
        s.write_text("import sys; sys.exit(7)\n", encoding="utf-8")
        with mock.patch.object(local, "HERE", self.tmp):
            self.assertEqual(local.script("s.py", []), 7)

    def test_reexec_venv(self):
        with mock.patch.dict(os.environ, {"ASK_CODE_PY": "/nonexistent/python"}), mock.patch.object(local.os, "execv") as ex:
            local.reexec_venv(["find"])
        ex.assert_not_called()
        with mock.patch.dict(os.environ, {"ASK_CODE_PY": sys.executable}, clear=False), mock.patch.object(local.os, "execv") as ex:
            os.environ.pop("ASK_CODE_REEXEC", None)
            self.addCleanup(os.environ.pop, "ASK_CODE_REEXEC", None)
            local.reexec_venv(["find", "q"])
        self.assertEqual(ex.call_args.args[0], sys.executable)
        self.assertEqual(ex.call_args.args[1][-2:], ["find", "q"])
        with mock.patch.dict(os.environ, {"ASK_CODE_PY": sys.executable, "ASK_CODE_REEXEC": "1"}), mock.patch.object(local.os, "execv") as ex:
            local.reexec_venv(["find"])
        ex.assert_not_called()

    def test_srv_version(self):
        self.assertEqual(float(local.srv_version()), max(os.path.getmtime(local.tool_path(f)) for f in local.FILES))

    def test_srv_connect_none_when_no_socket(self):
        with mock.patch.object(local, "SOCK", os.path.join(self.tmp, "nope.sock")):
            self.assertIsNone(local.srv_connect())

    def test_srv_connect_ok(self):
        path = os.path.join(self.tmp, "a.sock")
        srv = socket.socket(socket.AF_UNIX)
        self.addCleanup(srv.close)
        srv.bind(path)
        srv.listen(1)
        with mock.patch.object(local, "SOCK", path):
            c = local.srv_connect()
        self.assertIsNotNone(c)
        c.close()

    def test_srv_start_without_venv(self):
        with mock.patch.dict(os.environ, {"ASK_CODE_PY": "/nonexistent/python"}):
            self.assertIsNone(local.srv_start())

    def test_with_root(self):
        self.assertEqual(local.with_root(["q", "--root", "/x"]), ["q", "--root", "/x"])
        with mock.patch.object(ask_code, "find_root", return_value="/r"):
            self.assertEqual(local.with_root(["q"]), ["q", "--root", "/r"])


class DaemonTests(unittest.TestCase):
    """daemon_call against a fake server end of a socketpair."""

    def fake(self, reply):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        if reply is not None:
            a.sendall(reply)
        a.shutdown(socket.SHUT_WR)
        return b

    def test_unavailable(self):
        with mock.patch.object(local, "srv_connect", return_value=None), mock.patch.object(local, "srv_start", return_value=None):
            self.assertIsNone(local.daemon_call(["q"]))

    def test_ok_writes_out_and_err(self):
        reply = json.dumps({"out": "hit\n", "err": "warn\n", "code": 1}).encode() + b"\n"
        with mock.patch.object(local, "srv_connect", return_value=self.fake(reply)):
            rc, out, err = run(local.daemon_call, ["q"])
        self.assertEqual((rc, out, err), (1, "hit\n", "warn\n"))

    def test_closed_without_reply(self):
        with mock.patch.object(local, "srv_connect", return_value=self.fake(None)):
            self.assertIsNone(local.daemon_call(["q"]))

    def test_bad_json_reply(self):
        with mock.patch.object(local, "srv_connect", return_value=self.fake(b"garbage\n")):
            self.assertIsNone(local.daemon_call(["q"]))

    def test_restart_then_ok(self):
        first = self.fake(b'{"restart": true}\n')
        second = self.fake(json.dumps({"out": "", "err": "", "code": 0}).encode() + b"\n")
        with mock.patch.object(local, "srv_connect", side_effect=[first, second]), mock.patch("time.sleep"):
            rc, _, _ = run(local.daemon_call, ["q"])
        self.assertEqual(rc, 0)

    def test_restart_twice_gives_up(self):
        socks = [self.fake(b'{"restart": true}\n'), self.fake(b'{"restart": true}\n')]
        with mock.patch.object(local, "srv_connect", side_effect=socks), mock.patch("time.sleep"):
            self.assertIsNone(local.daemon_call(["q"]))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_find_and_index_in_process(self):
        with (
            mock.patch.dict(os.environ, {"LOCAL_NODAEMON": "1"}),
            mock.patch.object(local, "reexec_venv") as rx,
            mock.patch.object(ask_code, "main", return_value=0) as m,
        ):
            self.assertEqual(local.cmd_find(["q", "--root", "/x"]), 0)
            m.assert_called_with(["q", "--root", "/x"])
            rx.assert_called_with(["find", "q", "--root", "/x"])
            self.assertEqual(local.cmd_index(["--root", "/x"]), 0)
            m.assert_called_with(["--index", "--root", "/x"])
            rx.assert_called_with(["index", "--root", "/x"])

    def test_find_uses_daemon_answer(self):
        env = {k: v for k, v in os.environ.items() if k != "LOCAL_NODAEMON"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(local, "daemon_call", return_value=1) as dc,
            mock.patch.object(ask_code, "main") as m,
        ):
            self.assertEqual(local.cmd_find(["q", "--root", "/x"]), 1)
            self.assertEqual(local.cmd_index(["--root", "/x"]), 1)
        self.assertEqual(dc.call_args_list[1].args[0], ["--index", "--root", "/x"])
        m.assert_not_called()

    def test_find_falls_back_when_daemon_down(self):
        env = {k: v for k, v in os.environ.items() if k != "LOCAL_NODAEMON"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(local, "daemon_call", return_value=None),
            mock.patch.object(local, "reexec_venv"),
            mock.patch.object(ask_code, "main", return_value=1) as m,
        ):
            self.assertEqual(local.cmd_find(["q", "--root", "/x"]), 1)
        m.assert_called_once()

    def test_judge(self):
        f = Path(self.tmp, "a.py")
        f.write_text("x = 1\ny = 2\n", encoding="utf-8")
        g = Path(self.tmp, "b.py")
        g.write_text("z = 3\n", encoding="utf-8")
        answers = iter(['{"verdict":"PASS","reason":"ok","evidence":"x   =  1"}', '{"verdict":"FAIL","reason":"bad","evidence":"not in file"}'])
        with mock.patch.object(askmodel, "llm", side_effect=lambda *a, **k: next(answers)):
            rc, out, _ = run(local.cmd_judge, ["--criteria", "has x", str(f), str(g)])
        self.assertEqual(rc, 1)
        self.assertEqual(out.splitlines(), [f"PASS  {f}  ok  | x   =  1", f"FAIL  {g}  bad"])

    def test_judge_criteria_from_file_and_unknown_verdict(self):
        c = Path(self.tmp, "c.txt")
        c.write_text("be nice", encoding="utf-8")
        f = Path(self.tmp, "a.py")
        f.write_text("x\n", encoding="utf-8")
        with mock.patch.object(askmodel, "llm", return_value="no json") as llm:
            rc, out, _ = run(local.cmd_judge, [str(f), "--criteria", "@" + str(c)])
        self.assertEqual((rc, out), (1, f"?  {f}  \n"))
        self.assertIn("Criteria:\nbe nice", llm.call_args.args[0])

    def test_judge_needs_criteria(self):
        with self.assertRaises(SystemExit) as cm:
            local.cmd_judge(["a.py"])
        self.assertEqual(cm.exception.code, "judge needs --criteria")

    def test_review(self):
        b, d, t = (Path(self.tmp, n) for n in ("brief", "diff", "tests"))
        b.write_text("do x", encoding="utf-8")
        d.write_text("+x", encoding="utf-8")
        t.write_text("1 passed", encoding="utf-8")
        with mock.patch.object(askmodel, "llm", return_value='{"verdict":"PASS","reason":"fine"}') as llm:
            rc, out, _ = run(local.cmd_review, ["--brief", str(b), "--diff", str(d), "--tests", str(t)])
        self.assertEqual((rc, out), (0, "PASS  fine\n"))
        self.assertIn("1 passed", llm.call_args.args[0])
        self.assertTrue(llm.call_args.kwargs["think"])
        with mock.patch.object(askmodel, "llm", return_value='{"verdict":"FAIL","reason":"nope"}') as llm:
            rc, out, _ = run(local.cmd_review, ["--brief", str(b), "--diff", str(d)])
        self.assertEqual((rc, out), (1, "FAIL  nope\n"))
        self.assertIn("(not provided)", llm.call_args.args[0])

    def test_review_is_one_call_and_skips_over_the_token_budget(self):
        b, d = Path(self.tmp, "brief"), Path(self.tmp, "diff")
        b.write_text("do x", encoding="utf-8")
        f = "diff --git a/{0} b/{0}\n+" + "x" * 2500 + "\n"
        d.write_text(f.format("a") + f.format("b") + f.format("c"), encoding="utf-8")  # one call
        with mock.patch.object(askmodel, "llm", return_value='{"verdict":"PASS","reason":"ok"}') as llm:
            rc, out, _ = run(local.cmd_review, ["--brief", str(b), "--diff", str(d)])
        self.assertEqual((rc, llm.call_count), (0, 1))
        for name in "abc":
            self.assertIn(f"diff --git a/{name}", llm.call_args_list[0].args[0])  # the whole diff in the one call
        d.write_text("diff --git a/a b/a\n+" + "x" * 400000, encoding="utf-8")  # over the token budget
        with mock.patch.object(askmodel, "llm") as llm:
            rc, out, _ = run(local.cmd_review, ["--brief", str(b), "--diff", str(d)])
        self.assertEqual((rc, llm.call_count), (3, 0))  # a distinct code: callers map it to skip
        self.assertIn("est. tokens > budget", out)

    def test_review_lists_every_finding_and_still_reads_the_old_evidence_form(self):
        b, d = Path(self.tmp, "brief"), Path(self.tmp, "diff")
        b.write_text("do x", encoding="utf-8")
        d.write_text("diff --git a/a b/a\n+x\n", encoding="utf-8")
        many = '{"verdict":"FAIL","reason":"two","findings":[{"where":"a:1","quote":"x","why":"w1"},{"where":"a:2","quote":"y","why":"w|2"}]}'
        with mock.patch.object(askmodel, "llm", return_value=many) as llm:
            rc, out, _ = run(local.cmd_review, ["--brief", str(b), "--diff", str(d)])
        self.assertEqual((rc, llm.call_count, llm.call_args.args[1]), (1, 1, 5000))
        self.assertEqual(out.splitlines()[1:], ["w1  | a:1 x", "w/2  | a:2 y"])
        old = '{"verdict":"FAIL","reason":"r","evidence":"a:1 x"}'
        with mock.patch.object(askmodel, "llm", return_value=old):
            rc, out, _ = run(local.cmd_review, ["--brief", str(b), "--diff", str(d)])
        self.assertEqual(out.splitlines()[1:], ["r  | a:1 x"])

    def test_review_needs_args(self):
        with self.assertRaises(SystemExit) as cm:
            local.cmd_review([])
        self.assertEqual(cm.exception.code, "review needs --brief")
        b = Path(self.tmp, "brief")
        b.write_text("x", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            local.cmd_review(["--brief", str(b)])
        self.assertEqual(cm.exception.code, "review needs --diff")


class MainTests(unittest.TestCase):
    def test_usage(self):
        for a in ([], ["-h"], ["--help"], ["bogus"]):
            with self.assertRaises(SystemExit) as cm:
                local.main(a)
            self.assertEqual(cm.exception.code, local.__doc__)

    def test_dispatch(self):
        with mock.patch.object(local, "script", return_value=5) as sc:
            self.assertEqual(local.main(["nav", "def", "x"]), 5)
            sc.assert_called_with("lsp.py", ["def", "x"])
            self.assertEqual(local.main(["bulk", "a"]), 5)
            sc.assert_called_with("localmodel/localdev.py", ["bulk", "a"])
            self.assertEqual(local.main(["status"]), 5)
            sc.assert_called_with("localq.py", ["status"])
        for name, cmd in (("find", "cmd_find"), ("index", "cmd_index"), ("judge", "cmd_judge"), ("review", "cmd_review")):
            with mock.patch.object(local, cmd, return_value=9) as m, mock.patch("search.strata.strata_up", return_value=True):
                self.assertEqual(local.main([name, "x"]), 9)
            m.assert_called_with(["x"])


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.log = os.path.join(self.tmp, "u.jsonl")
        p = mock.patch.dict(os.environ, {"LP_USAGE_LOG": self.log})
        p.start()
        self.addCleanup(p.stop)

    def records(self):
        return [json.loads(x) for x in Path(self.log).read_text(encoding="utf-8").splitlines()]

    def test_usage_log_records(self):
        local.usage_log(["find", "where is x"], 1, 1.234, 3)
        local.usage_log(["nav", "def", "foo"], 0, 0.5, 1)
        local.usage_log([], 0, 0, 0)
        a, b, c = self.records()
        self.assertEqual((a["cmd"], a["idk"], a["q"], a["rc"], a["lines"], a["seconds"]), ("find", True, "where is x", 1, 3, 1.23))
        self.assertEqual((b["cmd"], b["idk"], b["q"]), ("nav def", False, ""))
        self.assertEqual(c["cmd"], "")
        self.assertEqual(a["root"], os.getcwd())

    def test_usage_log_never_raises(self):
        with mock.patch.dict(os.environ, {"LP_USAGE_LOG": "/proc/nope/x.jsonl"}):
            self.assertIsNone(local.usage_log(["find", "q"], 0, 0, 0))
            self.assertFalse(os.path.exists("/proc/nope"))

    def test_count_wrapper(self):
        buf = io.StringIO()
        c = local._Count(buf)
        c.write("a\nb\n")
        self.assertEqual(c.lines, 2)
        self.assertEqual(buf.getvalue(), "a\nb\n")
        self.assertTrue(c.getvalue() == "a\nb\n")  # attribute pass-through

    def test_run_logged_ok(self):
        saved = sys.stdout
        self.addCleanup(setattr, sys, "stdout", saved)

        def fake(argv):
            print("one")
            print("two")
            return 0

        with mock.patch.object(local, "main", side_effect=fake), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(local.run_logged(["status"]), 0)
        (rec,) = self.records()
        self.assertEqual((rec["cmd"], rec["rc"], rec["lines"]), ("status", 0, 2))

    def test_run_logged_system_exit_codes(self):
        saved = sys.stdout
        self.addCleanup(setattr, sys, "stdout", saved)
        for exc, rc in ((SystemExit(3), 3), (SystemExit(None), 0), (SystemExit("text"), 2)):
            with mock.patch.object(local, "main", side_effect=exc), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                local.run_logged(["status"])
            self.assertEqual(self.records()[-1]["rc"], rc)
        self.assertIs(sys.stdout, saved)


if __name__ == "__main__":
    unittest.main()
