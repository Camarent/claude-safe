import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.configuration import ENV_GUARD_ALLOW, ENV_GUARD_JOB, ENV_GUARD_LOG, ENV_GUARD_MAIN, ENV_GUARD_WORKTREE, ENV_SCRATCH_DIR
from tests.support.tmpdirs import tracked_mkdtemp

GUARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "write_guard.py")
GUARD_VARS = (ENV_GUARD_WORKTREE, ENV_GUARD_MAIN, ENV_GUARD_ALLOW, ENV_GUARD_JOB, ENV_GUARD_LOG)


def run_hook(payload, env_extra=None, stdin_text=None):
    """Run the hook with only the guard vars the test sets; the caller's guard env is dropped."""
    env = {k: v for k, v in os.environ.items() if k not in GUARD_VARS}
    env.update(env_extra or {})
    text = json.dumps(payload) if stdin_text is None else stdin_text
    # cwd outside any repo, so the hook reads only the payload
    return subprocess.run([sys.executable, "-I", GUARD], check=False, input=text, capture_output=True, text=True, timeout=30, env=env, cwd=os.sep)


class WriteGuardTest(unittest.TestCase):
    def setUp(self):
        t = tracked_mkdtemp(prefix="wguard-")
        self.t = os.path.realpath(t)
        self.main = f"{self.t}/claude-safe"
        self.wt = f"{self.main}/.wt/dev-cwd/task-2/claude-safe"
        self.sibling = f"{self.main}-old"  # shares the main prefix as a string, not as a directory
        for d in (self.wt, f"{self.main}/.work/dev-cwd/reports", self.sibling, f"{self.t}/scratch"):
            os.makedirs(d)
        self.env = {ENV_GUARD_WORKTREE: self.wt, ENV_GUARD_MAIN: self.main, ENV_GUARD_JOB: "dev-cwd/task-2"}

    def payload(self, path, tool="Write", key="file_path"):
        return {"tool_name": tool, "tool_input": {key: path}, "cwd": self.wt}

    def assert_denied(self, done):
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout, "expected a deny decision")
        return json.loads(done.stdout)["hookSpecificOutput"]

    def test_no_env_does_nothing(self):
        done = run_hook(self.payload(f"{self.main}/README.md"))
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_worktree_path_allowed(self):
        done = run_hook(self.payload(f"{self.wt}/a.py"), self.env)
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_unlisted_worktree_path_allowed(self):
        # a path no task file lists: the guard only fences the main checkout, never the job worktree
        done = run_hook(self.payload(f"{self.wt}/core/unlisted_module.py"), self.env)
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_report_path_allowed_when_listed(self):
        env = dict(self.env, **{ENV_GUARD_ALLOW: f"{self.main}/.work/dev-cwd/reports"})
        done = run_hook(self.payload(f"{self.main}/.work/dev-cwd/reports/report-a1.md"), env)
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_scratch_path_allowed(self):
        done = run_hook(self.payload(f"{self.t}/scratch/x.txt"), self.env)
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_main_path_denied(self):
        why = self.assert_denied(run_hook(self.payload(f"{self.main}/README.md"), self.env))["permissionDecisionReason"]
        self.assertIn(self.wt, why)
        self.assertIn(f"Retry once with the same relative path under the worktree: {self.wt}/README.md.", why)
        self.assertNotIn(ENV_SCRATCH_DIR, why)

    def test_main_scratch_path_names_scratch_dir(self):
        why = self.assert_denied(run_hook(self.payload(f"{self.main}/.work/dev-cwd/tmp/x.txt"), self.env))["permissionDecisionReason"]
        self.assertIn(f"{self.wt}/.work/dev-cwd/tmp/x.txt", why)
        self.assertIn(f"${ENV_SCRATCH_DIR}", why)

    def test_dotdot_escape_denied(self):
        self.assert_denied(run_hook(self.payload(f"{self.wt}/../../../README.md"), self.env))

    def test_sibling_sharing_prefix_allowed(self):
        done = run_hook(self.payload(f"{self.sibling}/x.py"), self.env)
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_multiedit_and_notebook_checked(self):
        self.assert_denied(run_hook(self.payload(f"{self.main}/a.py", tool="MultiEdit"), self.env))
        self.assert_denied(run_hook(self.payload(f"{self.main}/n.ipynb", tool="NotebookEdit", key="notebook_path"), self.env))

    def test_other_tool_ignored(self):
        done = run_hook(self.payload(f"{self.main}/README.md", tool="Bash"), self.env)
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_denial_logged(self):
        log = f"{self.t}/guard.jsonl"
        run_hook(self.payload(f"{self.main}/README.md"), dict(self.env, **{ENV_GUARD_LOG: log}))
        with open(log, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"ts", "job", "tool", "path"})
        self.assertEqual((rows[0]["job"], rows[0]["tool"]), (self.env[ENV_GUARD_JOB], "Write"))
        self.assertEqual(os.path.relpath(rows[0]["path"], self.main), "README.md")

    def test_log_failure_keeps_deny(self):
        bad = f"{self.t}/missing-dir/guard.jsonl"
        done = run_hook(self.payload(f"{self.main}/README.md"), dict(self.env, **{ENV_GUARD_LOG: bad}))
        self.assertEqual(done.returncode, 0)
        self.assertIn("permissionDecision", done.stdout)

    def test_malformed_input_blocks(self):
        done = run_hook(None, self.env, stdin_text="not json")
        self.assertEqual(done.returncode, 2)


if __name__ == "__main__":
    unittest.main()
