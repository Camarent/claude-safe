import io
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ux_run
from tests.support.tmpdirs import tracked_mkdtemp


class UxRunTest(unittest.TestCase):
    def test_block_vars_and_placeholder(self):
        b = ux_run.block("x\n```ux\nbuild: ./b <course>\nurl: http://h:1/\n```\n")
        self.assertEqual(b, {"build": "./b <course>", "url": "http://h:1/"})
        v = ux_run.variables("UX-VARS: course=fr a=b\n", ["a=c"])
        self.assertEqual((v["course"], v["a"]), ("fr", "c"))
        self.assertEqual(ux_run.sub(b["build"], v), "./b fr")

    def test_no_block_fails_clearly(self):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/s")
        self.assertEqual(ux_run.main(["--stage", "s", "--dir", d]), 1)

    def test_any_http_answer_means_reachable(self):
        err = urllib.error.HTTPError("http://h:1/", 404, "nf", None, None)  # type: ignore[arg-type]  # no headers needed
        with mock.patch.object(ux_run.urllib.request, "urlopen", side_effect=err):
            self.assertTrue(ux_run.up("http://h:1/"))
        with mock.patch.object(ux_run.urllib.request, "urlopen", side_effect=OSError("refused")):
            self.assertFalse(ux_run.up("http://h:1/"))

    def _project(self, audit_block):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/s")
        with open(f"{d}/AUDIT.md", "w", encoding="utf-8") as f:
            f.write("```ux\n" + audit_block + "\n```\n")
        return d

    def test_build_timeout_is_a_fail_verdict(self):
        d = self._project("build: python3 -c 'import time; time.sleep(30)'\nurl: http://127.0.0.1:1/")
        out = io.StringIO()
        with mock.patch.object(ux_run, "BUILD_TIMEOUT_S", 0.5), redirect_stdout(out):
            self.assertEqual(ux_run.main(["--stage", "s", "--dir", d]), 1)
        self.assertTrue(out.getvalue().startswith("VERDICT: FAIL"))
        self.assertIn("timed out", out.getvalue())

    def test_killpg_race_keeps_done_verdict(self):
        d = self._project("serve: python3 -c 'pass'\nurl: http://127.0.0.1:1/")
        out = io.StringIO()
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(ux_run, "up", return_value=True),
            mock.patch.object(ux_run.subprocess, "run", return_value=done),
            mock.patch.object(ux_run.os, "killpg", side_effect=ProcessLookupError()),
            redirect_stdout(out),
        ):
            self.assertEqual(ux_run.main(["--stage", "s", "--dir", d]), 0)
        self.assertTrue(out.getvalue().startswith("VERDICT: DONE"))

    def run_main(self, block):
        d = self._project(block)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = ux_run.main(["--stage", "s", "--dir", d])
        return rc, out.getvalue()

    def test_failed_build_shows_its_output(self):
        bad = mock.Mock(returncode=2, stdout="", stderr="compile error X")
        with mock.patch.object(ux_run.subprocess, "run", return_value=bad):
            rc, out = self.run_main("build: make\nurl: http://127.0.0.1:1/")
        self.assertEqual(rc, 1)
        self.assertIn("build failed: compile error X", out)

    def test_build_that_cannot_start_is_a_fail_verdict(self):
        with mock.patch.object(ux_run.subprocess, "run", side_effect=OSError("no make")):
            rc, out = self.run_main("build: make\nurl: http://127.0.0.1:1/")
        self.assertEqual(rc, 1)
        self.assertIn("build could not start: no make", out)

    def test_unreachable_server_fails_after_the_polls(self):
        with mock.patch.object(ux_run, "up", return_value=False), mock.patch.object(ux_run, "time") as clock:
            rc, out = self.run_main("url: http://127.0.0.1:1/")
        self.assertEqual(rc, 1)
        self.assertIn("server not reachable at http://127.0.0.1:1/", out)
        # Only ux_run sees the fake clock: patching time.sleep itself also counts the sleeps of subprocess.run(timeout=) polling in project()'s git calls.
        self.assertEqual(clock.sleep.call_count, 30)

    def test_failing_screenshot_run_is_a_fail_verdict(self):
        bad = mock.Mock(returncode=1, stdout="", stderr="no chromium")
        with mock.patch.object(ux_run, "up", return_value=True), mock.patch.object(ux_run.subprocess, "run", return_value=bad):
            rc, out = self.run_main("url: http://127.0.0.1:1/")
        self.assertEqual(rc, 1)
        self.assertIn("ux_shots failed: no chromium", out)

    def test_flows_file_is_passed_as_steps_and_a_bad_one_fails(self):
        import json

        d = self._project("serve: python3 -c 'pass'\nurl: http://127.0.0.1:1/")
        with open(f"{d}/pipeline.json", "w", encoding="utf-8") as f:
            json.dump({"ux_flows": "flows.json"}, f)
        with open(f"{d}/flows.json", "w", encoding="utf-8") as f:
            json.dump([{"name": "home", "actions": []}], f)
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(ux_run, "up", return_value=True),
            mock.patch.object(ux_run.subprocess, "run", return_value=done) as run,
            mock.patch.object(ux_run.os, "killpg"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(ux_run.main(["--stage", "s", "--dir", d]), 0)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--steps") + 1], f"{d}/flows.json")
        with open(f"{d}/flows.json", "w", encoding="utf-8") as f:
            f.write("[]")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ux_run.main(["--stage", "s", "--dir", d]), 1)
        self.assertIn("ux_flows", out.getvalue())

    def test_flows_path_in_a_nested_repo_uses_the_stage_vars(self):
        import json

        d = self._project("serve: python3 -c 'pass'\nurl: http://127.0.0.1:1/")
        with open(f"{d}/pipeline.json", "w", encoding="utf-8") as f:
            json.dump({"ux_flows": "workspace/<course>/flows.json"}, f)
        with open(f"{d}/.work/s/TASKS.md", "w", encoding="utf-8") as f:
            f.write("UX-VARS: course=n5\n")
        os.makedirs(f"{d}/workspace/n5")
        with open(f"{d}/workspace/n5/flows.json", "w", encoding="utf-8") as f:
            json.dump([{"name": "home", "actions": []}], f)
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(ux_run, "up", return_value=True),
            mock.patch.object(ux_run.subprocess, "run", return_value=done) as run,
            mock.patch.object(ux_run.os, "killpg"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(ux_run.main(["--stage", "s", "--dir", d]), 0)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--steps") + 1], f"{d}/workspace/n5/flows.json")


if __name__ == "__main__":
    unittest.main()

    def test_flows_path_outside_the_repo_fails(self):
        import json

        d = self._project("serve: python3 -c 'pass'\nurl: http://127.0.0.1:1/")
        with open(f"{d}/pipeline.json", "w", encoding="utf-8") as f:
            json.dump({"ux_flows": "../outside.json"}, f)
        out = io.StringIO()
        with mock.patch.object(ux_run, "up", return_value=True), redirect_stdout(out):
            self.assertEqual(ux_run.main(["--stage", "s", "--dir", d]), 1)
        self.assertIn("inside the repo", out.getvalue())
