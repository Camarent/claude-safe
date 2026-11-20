import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace

import review_guard
from pipeline import driver
from pipeline.configuration import ENV_REVIEW_DIR, ENV_SCRATCH_DIR

GUARD = os.path.abspath(review_guard.__file__)


def run_hook(stdin_text, root=None):
    env = {k: v for k, v in os.environ.items() if k != ENV_REVIEW_DIR}
    if root:
        env[ENV_REVIEW_DIR] = root
    return subprocess.run([sys.executable, "-I", GUARD], check=False, input=stdin_text, capture_output=True, text=True, timeout=30, env=env)


class GuardTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = os.path.realpath(t.name)
        self.copy, self.job = f"{self.t}/jobwt", f"{self.t}/other"
        os.makedirs(self.copy)
        os.makedirs(self.job)
        os.symlink(self.job, f"{self.copy}/escape")

    def edit(self, path, tool="Edit"):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        return review_guard.verdict({"tool_name": tool, "tool_input": {key: path}}, self.copy)

    def bash(self, cmd, cwd=None):
        return review_guard.verdict({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd or self.copy}, self.copy)

    def test_edit_allowed_inside_copy(self):
        for tool in ("Edit", "Write", "NotebookEdit"):
            self.assertEqual(self.edit(f"{self.copy}/a/b.py", tool), "")

    def stage_scratch(self):
        d = f"{self.t}/.work/s/tmp"
        os.makedirs(d)
        os.makedirs(f"{self.t}/.work/s/audit")
        os.symlink(f"{self.t}/.work/s/audit", f"{d}/out")
        return d

    def test_stage_scratch_writes_allowed(self):
        d = self.stage_scratch()
        with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: d}):
            self.assertEqual(self.bash(f"grep -r x . | sort > {d}/ids.txt"), "")
            self.assertEqual(self.bash(f"touch {d}/a && cp x {d}/b && rm {d}/a"), "")
            self.assertEqual(self.bash(f"cp x -t {d}"), "")
            self.assertEqual(self.edit(f"{d}/n.txt", "Write"), "")

    def test_stage_scratch_escapes_denied(self):
        d = self.stage_scratch()
        with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: d}):
            self.assertIn("outside", self.bash(f"echo x > {d}/../audit/x.md"))
            self.assertIn("outside", self.bash(f"echo x > {d}/out/x.md"))
            self.assertIn("outside", self.bash(f"touch {self.t}/.work/s/audit/x.md"))
            self.assertIn("outside", self.bash(f"echo x > {d}/$HOME/x"))
            self.assertIn("outside", self.edit(f"{d}/../audit/x.md", "Write"))
            self.assertIn("outside", self.edit(f"{d}/out/x.md", "Edit"))
        for bad in ("", os.sep, self.t):
            with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: bad}):
                self.assertIn("outside", self.bash(f"echo x > {d}/x"))
                self.assertIn("outside", self.edit(f"{d}/x", "Write"))

    def test_scratch_python_script_judged_like_inline(self):
        """review_guard has no python rule: the scratch-script form passes as `python3 -I -c` does; a script elsewhere is judged by its redirects only."""
        d = self.stage_scratch()
        with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: d}):
            self.assertEqual(self.bash(f"python3 -I {d}/x.py a b"), self.bash("python3 -I -c 'print(1)'"))
            self.assertEqual(self.bash(f"python3 -I {d}/x.py"), "")
            self.assertNotEqual(self.bash(f"python3 -I {d}/x.py > {self.job}/o"), "")

    def test_driver_sets_scratch_env(self):
        c = SimpleNamespace(dir=self.t, w=f"{self.t}/.work/s")
        env = driver.scratch_env(c)
        want = f"{self.t}/.work/s/tmp"
        self.assertEqual(env, {ENV_SCRATCH_DIR: want, "TMPDIR": want})
        self.assertTrue(os.path.isdir(want))
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump({"test_env": {"TMPDIR": "t"}}, f)
        self.assertEqual(driver.scratch_env(c), {ENV_SCRATCH_DIR: want})

    def test_phase_stage_report_allowed(self):  # review audit H1
        os.makedirs(f"{self.t}/.work/F/phases/01-a/reports")
        self.assertEqual(self.edit(f"{self.t}/.work/F/phases/01-a/reports/report-a1.md", "Write"), "")
        self.assertNotEqual(self.edit(f"{self.t}/elsewhere/reports/report-a1.md", "Write"), "")

    def test_report_file_allowed(self):
        os.makedirs(f"{self.t}/.work/s/reports")
        self.assertEqual(self.edit(f"{self.t}/.work/s/reports/report-r1.md", "Write"), "")
        self.assertIn("outside", self.edit(f"{self.t}/.work/s/task-1.md", "Write"))

    def test_edit_denied_in_job_worktree_and_escapes(self):
        self.assertIn("outside", self.edit(f"{self.job}/a.py"))
        self.assertIn("outside", self.edit(f"{self.copy}/../job/a.py"))
        self.assertIn("outside", self.edit(f"{self.copy}/escape/a.py"))
        self.assertIn("outside", self.edit(f"{self.copy}-evil/a.py"))

    def test_cd_prefix_stays_guarded(self):  # `cd` is allowed for headless sessions; the guard still judges what follows it, in the new cwd
        self.assertEqual(self.bash(f"cd {self.copy} && git -c safe.directory='*' status"), "")
        self.assertNotEqual(self.bash(f"cd {self.job} && rm -f x.py"), "")

    def test_cd_into_own_worktree_then_allowed_command(self):  # job-scope relaxation: own worktree only
        self.assertEqual(self.bash(f"cd {self.copy} && ls && git status"), "")
        self.assertEqual(self.bash(f"cd {self.copy} && echo ok > a.txt"), "")

    def test_cd_into_other_worktree_then_write_denied(self):
        self.assertNotEqual(self.bash(f"cd {self.copy}/../other && rm -f a.py"), "")  # `..` escape
        self.assertNotEqual(self.bash(f"cd {self.copy}/escape && rm -f a.py"), "")  # symlink escape
        self.assertNotEqual(self.bash(f"cd {self.copy} ; cd {self.job} && rm -f a.py"), "")
        self.assertNotEqual(self.bash(f"cd {self.copy} && rm -f a.py ; rm -f {self.job}/b"), "")

    def test_mkdir_in_scratch_allowed(self):  # mkdir/mktemp under TMPDIR, not elsewhere
        scratch = f"{self.t}/scratch"
        os.makedirs(scratch)
        with mock.patch.dict(os.environ, {"TMPDIR": scratch}):
            self.assertEqual(self.bash(f"mkdir -p {scratch}/x/y"), "")
            self.assertEqual(self.bash(f"mktemp -d {scratch}/tmp.XXXX"), "")
            self.assertEqual(self.bash("mktemp -d"), "")

    def test_mkdir_outside_worktree_allowed(self):  # owner decision: mkdir is not path-checked for reviewers
        scratch = f"{self.t}/scratch"
        os.makedirs(scratch)
        with mock.patch.dict(os.environ, {"TMPDIR": scratch}):
            self.assertEqual(self.bash(f"mkdir -p {self.job}/x"), "")
            self.assertEqual(self.bash(f"mkdir {scratch}/../job/y"), "")
            self.assertEqual(self.bash("mkdir /nonexistent-dir-probe/x"), "")
        with mock.patch.dict(os.environ, {"TMPDIR": ""}):
            self.assertEqual(self.bash(f"mkdir {scratch}/x"), "")

    def test_mkdir_chained_with_write_still_denied(self):  # the mkdir allowance does not cover a write after it
        scratch = f"{self.t}/scratch"
        os.makedirs(scratch)
        with mock.patch.dict(os.environ, {"TMPDIR": scratch}):
            self.assertIn("outside", self.bash(f"mkdir {scratch}/a && rm -f {self.job}/b"))
            self.assertIn("outside", self.bash(f"mkdir {scratch}/a && cp x {self.job}/../out"))

    def test_cat_and_python_inline_allowed(self):  # owner decision: reads and `python3 -I -c` pass the reviewer guard
        self.assertEqual(self.bash("cat a.py"), "")
        self.assertEqual(self.bash('python3 -I -c "print(1)"'), "")
        self.assertEqual(self.bash("python3 -I -"), "")

    def test_mktemp_outside_scratch_denied(self):
        scratch = f"{self.t}/scratch"
        os.makedirs(scratch)
        with mock.patch.dict(os.environ, {"TMPDIR": scratch}):
            self.assertIn("outside", self.bash(f"mktemp -p {self.job}"))
            self.assertIn("outside", self.bash(f"mktemp {self.job}/x.XXXX"))
            self.assertIn("outside", self.bash(f"mktemp --tmpdir={self.job} -d"))
            self.assertIn("outside", self.bash(f"mktemp -d {scratch}/../other/t.XXXX"))  # `..` escape into the job dir

    def test_snip_run_judged_as_wrapped_command(self):
        self.assertEqual(self.bash(f"snip run git -C {self.copy} status"), "")
        self.assertEqual(self.bash(f"snip run cd {self.copy} && ls"), "")
        self.assertIn("outside", self.bash(f"snip run rm -rf {self.job}"))
        self.assertIn("outside", self.bash(f"snip run sh -c 'rm -f {self.job}/x'"))
        self.assertIn("outside", self.bash(f"snip run cd {self.job} && rm -f a.py"))  # the cwd moves inside snip too
        self.assertNotEqual(self.bash("snip run git commit -m x"), "")
        self.assertNotEqual(self.bash("snip run xargs rm"), "")

    def test_unresolvable_cd_fails_closed_for_writes(self):  # review2 tokens (1)
        for cd in ("cd", "cd -", "cd $HOME", 'cd "$HOME"', "cd ~root", "cd -P -- /tmp", "cd `pwd`"):
            self.assertNotEqual(self.bash(f"{cd} && echo x > f"), "", cd)
            self.assertNotEqual(self.bash(f"{cd} && touch f"), "", cd)
        self.assertEqual(self.bash(f"cd - && echo x > {self.copy}/f"), "")

    def test_git_c_form_from_cd_rewrite_judged_like_cd_form(self):  # cmd_guard rewrites `cd D && git x` to `git -C D x`
        for c in (f"git -C {self.job} checkout -- .", f"git -C {self.job} restore .", f"git -C {self.job} reset --hard"):
            self.assertNotEqual(self.bash(c), "", c)
        self.assertNotEqual(self.bash(f"cd {self.job} && git checkout -- ."), "")
        self.assertEqual(self.bash(f"git -C {self.copy} status"), "")

    def test_redirect_to_own_report_allowed(self):
        work, main = f"{self.t}/.work/s", f"{self.t}/main"
        rep = f"{work}/phases/02-core/reports"
        os.makedirs(rep)
        os.makedirs(main)
        os.symlink(self.job, f"{work}/phases/02-core/reports/link.md")  # a symlink out of reports
        for c in (f"printf x > {rep}/r.md", f"printf x >> {rep}/r.md", f"mkdir -p {rep} && printf x > {rep}/r.md && ls {rep}"):
            self.assertEqual(self.bash(c), "", c)
        for c in (
            f"printf x > {work}/audit/f.md",
            f"printf x > {rep}/link.md",
            f"printf x > {rep}/sub/f.md",
            f"printf x > {rep}/../f.md",
            f"printf x > {main}/f.md",
        ):
            self.assertNotEqual(self.bash(c), "", c)

    def test_xargs_read_only_allowed_writers_denied(self):
        for c in ("grep -l C4 r/*.md | xargs ls -t | head -3", "ls | xargs -n1 cat", "ls | xargs -I{} head -5 {}", "ls | xargs wc -l"):
            self.assertEqual(self.bash(c), "", c)
        for c in (
            "ls | xargs rm",
            "ls | xargs sh -c 'rm x'",
            "ls | xargs -I{} cp {} /etc",
            "ls | xargs sed -i s/a/b/",
            "ls | xargs",
            "ls | xargs file -C",
            "ls | xargs -- rm",
        ):
            self.assertNotEqual(self.bash(c), "", c)

    def test_git_undo_denied_when_git_is_pointed_elsewhere(self):  # review2 fixes (2)
        for c in (
            f"git --work-tree={self.job} restore .",
            f"git --work-tree {self.job} restore .",
            f"GIT_WORK_TREE={self.job} git restore .",
            f"git --git-dir={self.job}/.git restore .",
        ):
            self.assertNotEqual(self.bash(c), "", c)
        self.assertEqual(self.bash("git restore ."), "")

    def test_readonly_git_false_denies_fixed(self):  # review3 fixes (2)
        for c in ("git stash show -p", "git stash show --stat", "git config --local --get user.name", "git config --global --list", "git config user.name"):
            self.assertEqual(self.bash(c), "", c)
        for c in ("git config user.name bob", "git config --local user.name bob", "git config --local --unset user.name"):
            self.assertNotEqual(self.bash(c), "", c)

    def test_git_config_env_and_command_keys_denied(self):  # review4 L2/L3
        for c in (
            "git --config-env=core.worktree=X checkout -- f",
            "git --config-env core.worktree=X checkout -- f",
            "git -c core.fsmonitor=cmd status",
            "git -c core.sshCommand=cmd log",
            "git -c core.pager=cmd log",
            "git -c alias.x='!cmd' x",
            "git -c credential.helper=cmd status",
        ):
            self.assertNotEqual(self.bash(c), "", c)
        self.assertEqual(self.bash("git -c color.ui=never status"), "")
        self.assertEqual(self.bash("git -c alias.x=log status"), "")

    def test_git_moved_by_config_or_export_denies_undo(self):  # review3 fixes (6)
        for c in (
            f"git -c core.worktree={self.job} restore .",
            f"git -ccore.worktree={self.job} restore .",
            f"export GIT_DIR={self.job}/.git; git restore .",
            f"export GIT_WORK_TREE={self.job} && git restore .",
        ):
            self.assertNotEqual(self.bash(c), "", c)
        self.assertEqual(self.bash("git -c color.ui=never restore ."), "")

    def test_readonly_git_is_exact(self):  # review2 fixes (3)
        for c in (
            "git branch -D develop -v",
            "git stash push -m list",
            "git tag -d v1 -l",
            "git config k v --list",
            "git worktree add p list",
            "git remote set-url origin X show",
            "git branch -m a b --list",
            "git branch x",
        ):
            self.assertNotEqual(self.bash(c), "", c)
        for c in (
            "git branch",
            "git branch -a",
            "git branch --list 'x*'",
            "git branch --contains HEAD",
            "git tag -l",
            "git remote -v",
            "git remote show origin",
            "git stash list",
            "git worktree list",
            "git config --get user.name",
            "git config --list",
        ):
            self.assertEqual(self.bash(c), "", c)

    def test_target_directory_is_checked(self):  # review2 fixes (7)
        for c in (f"cp -t {self.job} a", f"cp --target-directory={self.job} a", f"install -t {self.job} f", f"ln -st {self.job} f", f"cp -t{self.job} a"):
            self.assertNotEqual(self.bash(c), "", c)
        self.assertEqual(self.bash(f"cp -t {self.copy}/d a"), "")

    def test_bash_writes_outside_denied(self):
        for cmd in (
            f"echo x > {self.job}/f",
            f"echo x | tee {self.job}/f",
            f"sed -i s/a/b/ {self.job}/f",
            f"rm -rf {self.job}",
            f"bash -c 'echo x > {self.job}/f'",
            "echo x > $HOME/f",
            "git commit -am x",
            "git -C . reset --hard",
            "git checkout -b x",
            f"git -C {self.job} checkout -- f",
            "git stash",
            "git branch -D x",
            "for f in a; do rm /etc/x; done",
            "if true; then rm /etc/x; fi",
            "ls | xargs rm",
            "find . -delete",
            "git diff --output=/etc/x",
            "git push origin x",
            "git stash",
            "python3 -I /x/tools/wt.py review-reset --stage s --task t",
            "python3 -I /x/tools/commit.py x",
        ):
            self.assertTrue(self.bash(cmd), cmd)

    def test_bash_inside_allowed(self):
        for cmd in (
            "git diff",
            "git status",
            "git log -1",
            "sed -i s/a/b/ f",
            "echo x > f.txt",
            "echo x > /dev/null 2>&1",
            "ls && grep x f",
            "git checkout -- f",
            "git restore f",
            "git branch --show-current",
            "git stash list",
            "git remote -v",
            "git config --get x",
            "git tag -l",
            "sed -i '/foo/d' f.txt",
            "cp /main/file.txt .",
            "grep '>' src/*.js",
            "for f in a; do rm x; done",
        ):
            self.assertEqual(self.bash(cmd), "", cmd)

    def test_hook_fail_closed_on_bad_input(self):
        for bad in ("not json", "[]", json.dumps({"tool_name": "Edit", "tool_input": []}), json.dumps({"tool_name": "Edit", "tool_input": {}})):
            r = run_hook(bad, self.copy)
            self.assertTrue(r.returncode == 2 or "deny" in r.stdout, bad)

    def test_hook_denies_and_allows_via_process(self):
        r = run_hook(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": f"{self.job}/a"}}), self.copy)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        r = run_hook(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": f"{self.copy}/a"}}), self.copy)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_other_sessions_unaffected(self):
        for bad in ("not json", json.dumps({"tool_name": "Edit", "tool_input": {"file_path": f"{self.job}/a"}})):
            r = run_hook(bad)
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_unguarded_reviewer_agent_is_fail_closed(self):
        ok = {"tool_name": "Write", "tool_input": {"file_path": f"{self.t}/.work/s/reports/report-r1.md"}, "agent_type": "reviewer"}
        r = run_hook(json.dumps(ok))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        for bad in (
            {"tool_name": "Edit", "tool_input": {"file_path": f"{self.t}/main.py"}},
            {"tool_name": "Bash", "tool_input": {"command": "echo x > main.py"}, "cwd": self.t},
        ):
            r = run_hook(json.dumps({**bad, "agent_type": "reviewer"}))
            self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny", bad)
        r = run_hook(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": f"{self.t}/main.py"}, "agent_type": "dev"}))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_host_prefixed_reviewer_is_guarded_too(self):
        bad = {"tool_name": "Edit", "tool_input": {"file_path": f"{self.t}/main.py"}, "agent_type": "pl-reviewer"}
        r = run_hook(json.dumps(bad))
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        for other in ("pl-ux-reviewer", "code-reviewer", "not-a-reviewer-agent"):
            r = run_hook(json.dumps({**bad, "agent_type": other}))
            self.assertEqual((r.returncode, r.stdout), (0, ""), other)
        r = run_hook(json.dumps({**bad, "agent_type": "dev"}))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_settings_register_guard_after_snip(self):
        s = json.load(open(os.path.join(os.path.dirname(os.path.dirname(GUARD)), "settings.json")))
        pre = s["hooks"]["PreToolUse"]
        cmds = [h["hooks"][0]["command"] for h in pre]
        self.assertIn("snip hook", cmds[0])
        i = next(i for i, c in enumerate(cmds) if "cs-review-guard" in c)
        self.assertEqual(pre[i]["matcher"], "Edit|Write|NotebookEdit|Bash")
        with mock.patch.dict(os.environ, {}, clear=False):
            self.assertGreater(i, 0)


if __name__ == "__main__":
    unittest.main()
