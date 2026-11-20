"""Characterization tests for pipeline/extra_tools.py: command building, check/outdated/main with every subprocess stubbed (nothing is installed)."""

import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import extra_tools as x

TABLE_TEXT = """# Extra tools
## Accepted
| Tool | Version | Why | Install | Licence | Network use | Vetted |
|---|---|---|---|---|---|---|
| vulture | 2.16 | dead code | `uv tool install vulture` | MIT | none | ACCEPT 2026-10-24 |
| cargo-audit | 0.22.2 | rust | `cargo install cargo-audit --locked` | MIT | none | ACCEPT 2026-10-24 |
| knip | 6.42.1 | js | `npm i --ignore-scripts knip@6.42.1` | ISC | none | ACCEPT 2026-10-24 |
| clangd | 1:19.0-63 | cpp | `apt-get install -y clangd=1:19.0-63` | x | none | ACCEPT 2026-10-29 |
| weird | 1.0 | other | `make install` | x | none | ACCEPT 2026-10-29 |
| pending | 1.0 | no | `uv tool install pending` | x | none | PENDING |
| bad name | b ad | no | `uv tool install x` | x | none | ACCEPT |
## Recommended
| late | 1.0 | no | `uv tool install late` | x | none | ACCEPT |
"""


def call_main(argv):
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            x.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


class Rows(unittest.TestCase):
    def test_kinds_and_filters(self):
        self.assertEqual(
            x.rows(TABLE_TEXT),
            [("vulture", "2.16", "uv"), ("cargo-audit", "0.22.2", "cargo"), ("knip", "6.42.1", "npm"), ("clangd", "1:19.0-63", "apt")],
        )


class Commands(unittest.TestCase):
    def test_version_cmd(self):
        self.assertEqual(x.version_cmd("clangd", "apt"), ["dpkg-query", "-W", "-f", "${Version}", "clangd"])
        self.assertEqual(x.version_cmd("cargo-audit", "cargo"), ["cargo", "audit", "--version"])
        self.assertEqual(x.version_cmd("knip", "npm"), [f"{x.JS}/node_modules/.bin/knip", "--version"])
        with mock.patch.object(x.os.path, "exists", return_value=True):
            self.assertEqual(x.version_cmd("vulture", "uv"), ["/usr/local/bin/vulture", "--version"])
        with mock.patch.object(x.os.path, "exists", return_value=False):
            self.assertEqual(x.version_cmd("vulture", "uv"), [os.path.expanduser("~") + "/.local/bin/vulture", "--version"])

    def test_has_finds_bumped_uv_tool_next_to_older_baked_one(self):
        user_bin = os.path.expanduser("~") + "/.local/bin/ruff"

        def fake_run(cmd, timeout=60):
            return (0, "ruff 0.17.4") if cmd[0] == user_bin else (0, "ruff 0.17.3")

        with mock.patch.object(x.os.path, "exists", return_value=True), mock.patch.object(x, "run", fake_run):
            self.assertTrue(x.has("ruff", "0.17.4", "uv"))
            self.assertFalse(x.has("ruff", "0.17.9", "uv"))
            self.assertFalse(x.has("ruff", "0.17.4", "npm"))

    def test_install_cmd(self):
        self.assertEqual(x.install_cmd("clangd", "1", "apt"), ["apt-get", "install", "-y", "--no-install-recommends", "clangd=1"])
        self.assertEqual(x.install_cmd("vulture", "2", "uv"), ["uv", "tool", "install", "vulture==2"])
        self.assertEqual(x.install_cmd("cargo-audit", "3", "cargo"), ["cargo", "install", "cargo-audit@3", "--locked"])
        self.assertEqual(x.install_cmd("knip", "4", "npm"), ["npm", "i", "--prefix", x.JS, "--ignore-scripts", "knip@4"])


class Run(unittest.TestCase):
    def test_run_real_subprocess_with_stub_and_errors(self):
        self.assertEqual(x.run([sys.executable, "-c", "print('hi')"]), (0, "hi"))
        rc, out = x.run([sys.executable, "-c", "import sys; sys.stderr.write('e'); sys.exit(3)"])
        self.assertEqual((rc, out), (3, "e"))
        rc, out = x.run(["/nonexistent/definitely-not-here"])
        self.assertEqual(rc, 127)
        self.assertTrue(out)
        rc, _ = x.run([sys.executable, "-c", "import time; time.sleep(5)"], 0.2)
        self.assertEqual(rc, 127)

    def test_run_finds_stub_on_fake_path(self):
        with tempfile.TemporaryDirectory() as d:
            stub = os.path.join(d, "faketool")
            with open(stub, "w") as f:
                f.write("#!/bin/sh\necho faketool 9.9\n")
            os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
            with mock.patch.dict(os.environ, {"PATH": d}):
                self.assertEqual(x.run(["faketool"]), (0, "faketool 9.9"))


class HasCheck(unittest.TestCase):
    def test_has_apt_needs_exact_version_others_substring(self):
        with mock.patch.object(x, "run", return_value=(0, "1:19.0-63")):
            self.assertTrue(x.has("clangd", "1:19.0-63", "apt"))
        with mock.patch.object(x, "run", return_value=(0, "1:19.0-63x")):
            self.assertFalse(x.has("clangd", "1:19.0-63", "apt"))
        with mock.patch.object(x, "run", return_value=(0, "vulture 2.16")):
            self.assertTrue(x.has("vulture", "2.16", "uv"))
        with mock.patch.object(x, "run", return_value=(1, "vulture 2.16")):
            self.assertFalse(x.has("vulture", "2.16", "uv"))

    def test_check_sorts_into_ok_done_bad_and_never_installs_without_flag(self):
        rs = [("a", "1", "uv"), ("b", "1", "uv"), ("c", "1", "uv")]
        present = {"a"}
        installed = set()

        def fake_has(n, v, k):
            return n in present or n in installed

        def fake_run(cmd, timeout=900):
            if cmd[-1].startswith("b"):
                installed.add("b")
                return 0, ""
            return 1, ""

        with mock.patch.object(x, "has", fake_has), mock.patch.object(x, "run", fake_run):
            self.assertEqual(x.check(rs, False), (["a 1"], [], ["b 1", "c 1"]))
            self.assertEqual(installed, set())
            self.assertEqual(x.check(rs, True), (["a 1"], ["b 1"], ["c 1"]))


class Scaffolds(unittest.TestCase):
    def test_ensure_proj_links_web_and_tools_and_writes_config(self):
        with tempfile.TemporaryDirectory() as d:
            project = os.path.join(d, "proj")
            os.makedirs(os.path.join(project, "web"))  # no tools/ dir: skipped
            js = os.path.join(d, "js")
            with mock.patch.object(x, "JS", js):
                x.ensure_proj(project)
                x.ensure_proj(project)  # second run replaces the symlink
            self.assertTrue(os.path.islink(f"{js}/proj/web"))
            self.assertFalse(os.path.exists(f"{js}/proj/tools"))
            with open(f"{js}/proj/eslint.config.js") as f:
                self.assertEqual(f.read(), x.ESLINT)
            with open(f"{js}/proj/package.json") as f:
                self.assertIn('"type": "module"', f.read())

    def test_ensure_browser_user_noop_when_not_root(self):
        with mock.patch.object(x.os, "geteuid", return_value=1000), mock.patch.object(x, "run") as r:
            x.ensure_browser_user()
        r.assert_not_called()

    def test_ensure_browser_user_as_root_creates_user_and_wrapper(self):
        calls = []

        def fake_run(cmd, timeout=900):
            calls.append(cmd[0])
            return (1, "") if cmd[0] == "id" else (0, "")

        with tempfile.TemporaryDirectory() as d:
            wrapper = os.path.join(d, "chrome-nonroot")
            real_makedirs = os.makedirs
            with (
                mock.patch.object(x.os, "geteuid", return_value=0),
                mock.patch.object(x, "run", fake_run),
                mock.patch.object(x, "WRAPPER", wrapper),
                mock.patch.object(x.os, "makedirs", lambda p, **k: real_makedirs(os.path.join(d, "mk"), **k)),
            ):
                x.ensure_browser_user()
            self.assertEqual(calls, ["id", "useradd", "chown"])
            with open(wrapper) as f:
                self.assertEqual(f.read(), x.WRAPPER_TEXT)
            self.assertTrue(os.access(wrapper, os.X_OK))


class Browser(unittest.TestCase):
    def run_with(self, results):
        seq = iter(results)
        seen = []

        def fake_run(cmd, timeout=900):
            seen.append(cmd)
            return next(seq)

        with mock.patch.object(x, "run", fake_run):
            return x.browser_ok(), seen

    def test_not_connected_or_missing_flag_fails_without_starting_chrome(self):
        ok, seen = self.run_with([(0, "chrome-devtools: x --no-sandbox - Failed")])
        self.assertFalse(ok)
        self.assertEqual(len(seen), 1)
        self.assertFalse(self.run_with([(0, "chrome-devtools: x - Connected")])[0])
        self.assertFalse(self.run_with([(1, "")])[0])

    def test_connected_runs_configured_chrome(self):
        ok, seen = self.run_with([(0, "chrome-devtools: chrome-nonroot --no-sandbox - Connected"), (0, "")])
        self.assertTrue(ok)
        self.assertEqual(seen[1][0], x.WRAPPER)
        ok, seen = self.run_with([(0, "chrome-devtools: foo --no-sandbox - Connected"), (1, "")])
        self.assertFalse(ok)
        self.assertEqual(seen[1][0], "google-chrome")


class Outdated(unittest.TestCase):
    def test_prints_latest_and_update_marker(self):
        outs = {
            "vulture": (0, "vulture (2.17, 2.16)"),
            "cargo-audit": (0, 'cargo-audit = "0.22.2"'),
            "knip": (1, ""),
            "clangd": (0, "  Candidate: 1:20.0-1\n"),
        }

        def fake_run(cmd, timeout=900):
            return outs[next(n for n in outs if n in cmd)]

        out = io.StringIO()
        with mock.patch.object(x, "run", fake_run), contextlib.redirect_stdout(out):
            x.outdated(x.rows(TABLE_TEXT))
        self.assertEqual(
            out.getvalue().splitlines(),
            [
                "vulture pinned 2.16 latest 2.17  UPDATE: sec-auditor must vet it first",
                "cargo-audit pinned 0.22.2 latest 0.22.2",
                "knip pinned 6.42.1 latest ?",
                "clangd pinned 1:19.0-63 latest 1:20.0-1  UPDATE: sec-auditor must vet it first",
            ],
        )


class SplitNeeded(unittest.TestCase):
    def test_split(self):
        self.assertEqual(x.split_needed(["Foo 1", "bar 2"], "uses foo"), (["Foo 1"], ["bar 2"]))
        self.assertEqual(x.split_needed(["foo 1"], None), ([], ["foo 1"]))


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.table = os.path.join(self.tmp.name, "EXTRA-TOOLS.md")
        with open(self.table, "w") as f:
            f.write(TABLE_TEXT)
        p = mock.patch.object(x, "TABLE", self.table)
        p.start()
        self.addCleanup(p.stop)

    def test_all_present_exit_zero_and_report_file(self):
        report = os.path.join(self.tmp.name, "r.txt")
        with mock.patch.object(x, "has", return_value=True):
            code, out = call_main(["check", "--report", report])
        self.assertEqual(code, 0)
        self.assertEqual(out, "VERDICT: DONE\nok: vulture 2.16, cargo-audit 0.22.2, knip 6.42.1, clangd 1:19.0-63\ninstalled: -\nfailed: -\n")
        with open(report) as f:
            self.assertEqual(f.read(), out)

    def test_missing_fails_and_kinds_filter(self):
        with mock.patch.object(x, "has", return_value=False), mock.patch.object(x, "run") as r:
            code, out = call_main(["check", "--kinds", "uv,apt"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "VERDICT: FAIL\nok: -\ninstalled: -\nfailed: vulture 2.16, clangd 1:19.0-63\n")
        r.assert_not_called()

    def test_install_flag_reports_installed(self):
        state = {"vulture": False}

        def fake_has(n, v, k):
            return n != "vulture" or state["vulture"]

        def fake_run(cmd, timeout=900):
            state["vulture"] = True
            return 0, ""

        with mock.patch.object(x, "has", fake_has), mock.patch.object(x, "run", fake_run):
            code, out = call_main(["check", "--install", "--kinds", "uv"])
        self.assertEqual((code, out), (0, "VERDICT: DONE\nok: -\ninstalled: vulture 2.16\nfailed: -\n"))

    def test_needed_by_turns_unneeded_failures_into_warnings(self):
        audit = os.path.join(self.tmp.name, "AUDIT.md")
        with open(audit, "w") as f:
            f.write("uses vulture\n```audit-tools\n```\n")
        with mock.patch.object(x, "has", return_value=False):
            code, out = call_main(["check", "--kinds", "uv,apt", "--needed-by", audit])
            self.assertEqual(code, 1)
            self.assertEqual(
                out,
                "VERDICT: FAIL\nok: -\ninstalled: -\nfailed: vulture 2.16\nwarning (not used by this project, install failed): clangd 1:19.0-63\n",
            )
            code, out = call_main(["check", "--kinds", "apt", "--needed-by", os.path.join(self.tmp.name, "missing.md")])
        self.assertEqual(code, 0)
        self.assertIn("warning (not used by this project, install failed): clangd 1:19.0-63", out)
        self.assertIn("no audit-tools block in", out)  # a missing AUDIT.md says so

    def test_audit_tools_note(self):
        d = self.tmp.name
        none, plain, block = (os.path.join(d, n) for n in ("none.md", "plain.md", "block.md"))
        with open(plain, "w") as f:
            f.write("uses vulture")
        with open(block, "w") as f:
            f.write("```audit-tools\nvulture\n```\n")
        self.assertIn(f"no audit-tools block in {none}", x.audit_tools_note(none))
        self.assertIn(f"no audit-tools block in {plain}", x.audit_tools_note(plain))
        self.assertEqual(x.audit_tools_note(block), "")

    def test_proj_and_browser_options(self):
        with (
            mock.patch.object(x, "has", return_value=True),
            mock.patch.object(x, "ensure_proj") as ep,
            mock.patch.object(x, "ensure_browser_user") as eb,
            mock.patch.object(x, "browser_ok", return_value=True),
        ):
            code, out = call_main(["check", "--kinds", "uv", "--proj", "/p", "--browser"])
        ep.assert_called_once_with("/p")
        eb.assert_called_once_with()
        self.assertEqual((code, out), (0, "VERDICT: DONE\nok: vulture 2.16, chrome-devtools MCP\ninstalled: -\nfailed: -\n"))
        with (
            mock.patch.object(x, "has", return_value=True),
            mock.patch.object(x, "ensure_browser_user"),
            mock.patch.object(x, "browser_ok", return_value=False),
        ):
            code, out = call_main(["check", "--kinds", "uv", "--browser"])
        self.assertEqual((code, out), (1, "VERDICT: FAIL\nok: vulture 2.16\ninstalled: -\nfailed: chrome-devtools MCP\n"))

    def test_outdated_subcommand(self):
        with mock.patch.object(x, "outdated", return_value=None) as o:
            self.assertEqual(x.main(["outdated"]), None)
        o.assert_called_once()


if __name__ == "__main__":
    unittest.main()
