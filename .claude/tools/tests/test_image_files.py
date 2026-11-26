"""The container suite mounts the whole checkout read-only; new tool files of the pipeline-tuning stage must be tracked and not ignored."""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(TOOLS))
MOUNT = '-v "$root":/src:ro'
PTEST = "/src/.claude/tools/ptest.py"
NEW_PATHS = [
    ".claude/tools/denial_log.py",
    ".claude/tools/run_summary.py",
    ".claude/tools/pipeline/steps_env.py",
    ".claude/tools/pipeline/steps_verify.py",
    ".claude/tools/pipeline/steps_finaltest.py",
    ".claude/tools/pipeline/steps_docfix.py",
    ".claude/tools/pipeline/configuration/tuning.py",
]
OPTIONAL_DIRS = [".claude/agents/architect", ".claude/agents/dev", ".claude/agents/reviewer"]  # agent split dirs, only if present


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def git(*args):
    env = {k: v for k, v in os.environ.items() if k.startswith("GIT_CONFIG_") or k in ("PATH", "HOME")}
    return subprocess.run(["git", "-C", ROOT, *args], check=False, capture_output=True, text=True, env=env)


class ImageSuiteMount(unittest.TestCase):
    def test_dockerfile_copies_no_tool_file(self):
        for line in read("Dockerfile").splitlines():
            if line.split()[:1] in (["COPY"], ["ADD"]):
                self.assertNotIn(".claude/tools", line)

    def test_release_runs_suite_on_read_only_checkout(self):
        lines = [ln for ln in read("scripts/release.sh").splitlines() if "docker run" in ln and MOUNT in ln and "--entrypoint python3" in ln]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(f"-I {PTEST} --dir /src/.claude/tools/tests", lines[0])
        self.assertNotIn(":/src:rw", lines[0])

    def test_release_has_no_writable_src_mount(self):
        for ln in read("scripts/release.sh").splitlines():
            if "docker run" in ln and "/src" in ln:
                self.assertIn(":/src:ro", ln)
                self.assertNotRegex(ln, r":/src(:rw)?(\s|$)")

    def test_image_test_script_mounts_read_only_and_runs_ptest(self):
        text = read("scripts/image_test.sh")
        self.assertIn('-v "$PWD":/src:ro', text)
        self.assertIn(PTEST, text)


class NewPathsTracked(unittest.TestCase):
    def paths(self):
        found = list(NEW_PATHS)
        found += [d for d in OPTIONAL_DIRS if os.path.isdir(os.path.join(ROOT, d))]
        return found

    def test_new_paths_exist(self):
        for rel in NEW_PATHS:
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_new_paths_tracked_and_not_ignored(self):
        for rel in self.paths():
            tracked = git("ls-files", "--error-unmatch", "--", rel)
            self.assertEqual(tracked.returncode, 0, f"{rel} is not tracked: {tracked.stderr}")
            # --no-index: without it check-ignore skips tracked paths, so a force-added ignored file would pass
            ignored = git("check-ignore", "-q", "--no-index", "--", rel)
            self.assertEqual(ignored.returncode, 1, f"{rel} is git-ignored")


if __name__ == "__main__":
    unittest.main()
