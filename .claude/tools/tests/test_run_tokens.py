"""run.sh forwards no host token unless its opt-in flag is given; each flag forwards only its own token."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(TOOLS))
RUN_SH = os.path.join(ROOT, "run.sh")
README = os.path.join(ROOT, "README.md")
GH_SECRET = "ghp_test_secret_value"
CF_SECRET = "cf_test_secret_value"

# Fake docker: `info` and `images` succeed (the image counts as built), `ps` prints nothing,
# `run` records its argv and the env it was started with, then exits 0.
FAKE_DOCKER = """#!/usr/bin/env bash
case "$1" in
    images) echo fake-image-id ;;
    run)
        printf '%s\\n' "$@" > "$FAKE_ARGV"
        env > "$FAKE_ENV"
        ;;
esac
exit 0
"""


class RunTokensTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.bin = os.path.join(self.tmp.name, "bin")
        self.work = os.path.join(self.tmp.name, "work")
        for d in (self.home, self.bin, self.work):
            os.makedirs(d)
        # run.sh writes .claude/, scratch/ and browser-profile/ next to itself: run a copy in the temp dir.
        self.script = os.path.join(self.tmp.name, "run.sh")
        shutil.copy(RUN_SH, self.script)
        self.argv_file = os.path.join(self.tmp.name, "argv")
        self.env_file = os.path.join(self.tmp.name, "env")
        fake = os.path.join(self.bin, "docker")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(FAKE_DOCKER)
        os.chmod(fake, 0o755)

    def run_script(self, *flags, **overrides):
        """Run the copied run.sh with the fake docker; a None override unsets that host variable.

        Returns (docker argv, stderr). The fake docker's env file is not asserted on: the host
        env is inherited by docker, so it cannot show whether run.sh forwarded a token.
        The `-e NAME` pair in argv is the guard.
        """
        env = {
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
            "HOME": self.home,
            "FAKE_ARGV": self.argv_file,
            "FAKE_ENV": self.env_file,
            "GH_TOKEN": GH_SECRET,
            "CLOUDFLARE_API_TOKEN": CF_SECRET,
        }
        for name, value in overrides.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        result = subprocess.run(
            [shutil.which("bash") or "bash", self.script, *flags],
            check=False,
            cwd=self.work,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(self.argv_file, encoding="utf-8") as f:
            argv = f.read().splitlines()
        return argv, result.stderr

    @staticmethod
    def passes(argv, name):
        """True when docker argv carries `-e NAME` (the value is never on argv)."""
        return any(argv[i] == "-e" and argv[i + 1] == name for i in range(len(argv) - 1))

    def assert_no_secret_in_argv(self, argv):
        joined = "\n".join(argv)
        self.assertNotIn(GH_SECRET, joined)
        self.assertNotIn(CF_SECRET, joined)

    def test_no_flag_forwards_no_token(self):
        argv, _ = self.run_script()
        self.assertFalse(self.passes(argv, "GH_TOKEN"))
        self.assertFalse(self.passes(argv, "CLOUDFLARE_API_TOKEN"))
        self.assert_no_secret_in_argv(argv)

    def test_with_gh_token_forwards_only_gh_token(self):
        argv, _ = self.run_script("--with-gh-token")
        self.assertTrue(self.passes(argv, "GH_TOKEN"))
        self.assertFalse(self.passes(argv, "CLOUDFLARE_API_TOKEN"))
        self.assert_no_secret_in_argv(argv)

    def test_with_cloudflare_token_forwards_only_cloudflare_token(self):
        argv, _ = self.run_script("--with-cloudflare-token")
        self.assertTrue(self.passes(argv, "CLOUDFLARE_API_TOKEN"))
        self.assertFalse(self.passes(argv, "GH_TOKEN"))
        self.assert_no_secret_in_argv(argv)

    def test_with_gh_token_but_no_host_token_warns(self):
        argv, stderr = self.run_script("--with-gh-token", GH_TOKEN=None, GITHUB_TOKEN=None)
        self.assertFalse(self.passes(argv, "GH_TOKEN"))
        self.assertIn("--with-gh-token given but GH_TOKEN and GITHUB_TOKEN are empty", stderr)

    def test_with_cloudflare_token_but_no_host_token_warns(self):
        argv, stderr = self.run_script("--with-cloudflare-token", CLOUDFLARE_API_TOKEN=None)
        self.assertFalse(self.passes(argv, "CLOUDFLARE_API_TOKEN"))
        self.assertIn("--with-cloudflare-token given but CLOUDFLARE_API_TOKEN is empty", stderr)

    def test_usage_lists_both_flags(self):
        result = subprocess.run(
            [shutil.which("bash") or "bash", RUN_SH, "--help"],
            check=False,
            cwd=self.work,
            env={"PATH": os.environ.get("PATH", ""), "HOME": self.home},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--with-gh-token", result.stdout)
        self.assertIn("--with-cloudflare-token", result.stdout)

    def test_readme_says_tokens_are_opt_in(self):
        with open(README, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("--with-gh-token", text)
        self.assertIn("--with-cloudflare-token", text)
        self.assertNotIn("host gh token is forwarded", text)


if __name__ == "__main__":
    unittest.main()
