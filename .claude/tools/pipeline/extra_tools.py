"""Extra tools (EXTRA-TOOLS.md, Accepted table): check the pinned versions, install only what the table lists.

    xtools.py check [--install] [--kinds K,K] [--browser] [--report FILE]   exit 0 all ok, 1 something missing/wrong/failed
    xtools.py outdated                            list newer upstream versions (network); never installs

Only an ACCEPT row with a pinned version is installed, with a command built from kind + name + version.
A newer version is a new tool for sec-auditor: vet it, bump the row, then `check --install` applies it.
"""

import argparse
import contextlib
import os
import re
import subprocess

from .ci_drift import drift
from .configuration import LONG_CMD_TIMEOUT_S, project
from .util import read_text, write_text

TABLE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "EXTRA-TOOLS.md")
JS = "/opt/jstools"


def rows(text):
    """-> [(name, version, kind)] of Accepted rows; kind from the Install column."""
    out = []
    for line in text.split("## Recommended")[0].splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) < 7 or not c[6].startswith("ACCEPT") or not re.fullmatch(r"[\w.:+~\-]+", c[1]):
            continue
        k = "uv" if "uv tool" in c[3] else "cargo" if "cargo install" in c[3] else "npm" if "npm i" in c[3] else "apt" if "apt-get" in c[3] else ""
        if k:
            out.append((c[0], c[1], k))
    return out


def _has_version_token(out, ver):
    """True when `ver` (optionally `v`-prefixed) stands alone in `out`: no word character or dot before or after."""

    def edge(ch):
        return bool(ch) and (ch.isalnum() or ch in "_.")

    start = out.find(ver)
    while start != -1:
        end = start + len(ver)
        before = out[start - 1] if start else ""
        lead = out[start - 2] if before == "v" and start > 1 else ""
        if (not before or not edge(before) or (before == "v" and not edge(lead))) and not (end < len(out) and edge(out[end])):
            return True
        start = out.find(ver, start + 1)
    return False


def version_cmd(name, kind):
    if kind == "apt":
        return ["dpkg-query", "-W", "-f", "${Version}", name]
    if kind == "uv":  # baked into the image: /usr/local/bin; installed by tools-check: ~/.local/bin
        baked = f"/usr/local/bin/{name}"
        return [baked if os.path.exists(baked) else f"{os.path.expanduser('~')}/.local/bin/{name}", "--version"]
    if kind == "cargo":
        return ["cargo", name.removeprefix("cargo-"), "--version"]
    return [f"{JS}/node_modules/.bin/{name}", "--version"]


def install_cmd(name, ver, kind):
    if kind == "apt":
        return ["apt-get", "install", "-y", "--no-install-recommends", f"{name}={ver}"]
    if kind == "uv":
        return ["uv", "tool", "install", f"{name}=={ver}"]
    if kind == "cargo":
        return ["cargo", "install", f"{name}@{ver}", "--locked"]
    return ["npm", "i", "--prefix", JS, "--ignore-scripts", f"{name}@{ver}"]


def run(cmd, timeout=LONG_CMD_TIMEOUT_S):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def version_cmds(name, kind):
    """Every place the pinned version may live. A uv tool baked into the image can be older than an owner bump that tools-check installed under ~/.local/bin."""
    cmds = [version_cmd(name, kind)]
    if kind == "uv":
        user = [f"{os.path.expanduser('~')}/.local/bin/{name}", "--version"]
        if user not in cmds:
            cmds.append(user)
    return cmds


def has(name, ver, kind):
    for cmd in version_cmds(name, kind):
        rc, out = run(cmd, 60)
        if rc == 0 and (out == ver if kind == "apt" else _has_version_token(out, ver)):
            return True
    return False


def check(rs, install):
    ok, done, bad = [], [], []
    for n, v, k in rs:
        if has(n, v, k):
            ok.append(f"{n} {v}")
        elif install and run(install_cmd(n, v, k))[0] == 0 and has(n, v, k):
            done.append(f"{n} {v}")
        else:
            bad.append(f"{n} {v}")
    return ok, done, bad


ESLINT = """export default [{ignores: ["**/vendor/**", "**/node_modules/**"]}, {
  files: ["**/*.js"],
  languageOptions: {ecmaVersion: "latest", sourceType: "module"},
  rules: {
    "max-lines": ["warn", {max: 300, skipBlankLines: true, skipComments: true}],
    "max-lines-per-function": ["warn", {max: 50, skipBlankLines: true, skipComments: true}],
    complexity: ["warn", 10], "max-depth": ["warn", 3], "max-params": ["warn", 4], "max-nested-callbacks": ["warn", 3],
  },
}];
"""


def ensure_proj(project):
    """eslint and knip scaffold /opt/jstools/proj (lost with the container): symlinks to the project's web/ and tools/, flat config from practices/js.md."""
    d = f"{JS}/proj"
    os.makedirs(d, exist_ok=True)
    for n in ("web", "tools"):
        t, src = f"{d}/{n}", os.path.join(os.path.abspath(project), n)
        if not os.path.isdir(src):
            continue
        if os.path.islink(t):
            os.remove(t)
        if not os.path.exists(t):
            os.symlink(src, t)
    write_text(f"{d}/eslint.config.js", ESLINT)
    write_text(f"{d}/knip.json", '{"ignore": ["eslint.config.js"]}\n')
    write_text(f"{d}/package.json", '{"name": "proj", "private": true, "type": "module"}\n')


WRAPPER = "/usr/local/bin/chrome-nonroot"
WRAPPER_TEXT = """#!/bin/sh
# Chrome as the unprivileged user `browser` (the MCP server runs as root). Used as chrome-devtools-mcp --executablePath.
exec runuser -u browser -- env HOME=/home/browser XDG_CONFIG_HOME=/home/browser/.config /usr/bin/google-chrome --disable-breakpad "$@"
"""


def ensure_browser_user():
    """Container rebuilds lose the `browser` user and its wrapper: recreate them (root only; the user namespace sandbox stays off)."""
    if os.geteuid() != 0:
        return
    if run(["id", "browser"])[0] != 0:
        run(["useradd", "-m", "-s", "/usr/sbin/nologin", "browser"])
    os.makedirs("/home/browser/chrome-profile", exist_ok=True)
    run(["chown", "-R", "browser:browser", "/home/browser"])
    if not os.path.exists(WRAPPER):
        write_text(WRAPPER, WRAPPER_TEXT)
        os.chmod(WRAPPER, 0o755)  # nosec B103  # wrapper script must be executable


def browser_ok():
    """headless web-tester / ux-reviewer need the chrome-devtools MCP server; `claude mcp list` shows it as Connected."""
    rc, out = run(["claude", "mcp", "list"], 120)
    line = next((row for row in out.splitlines() if row.startswith("chrome-devtools:")), "")
    if rc != 0 or "Connected" not in line or "--no-sandbox" not in line:  # Chrome's sandbox needs user namespaces, which the container does not allow
        return False
    exe = WRAPPER if "chrome-nonroot" in line else "google-chrome"  # the configured one, a non-root wrapper when set up
    rc, _ = run(
        [exe, "--headless=new", "--no-sandbox", "--disable-gpu", "--user-data-dir=/tmp/browser-ok", "--dump-dom", "about:blank"], 60
    )  # the browser itself starts
    return rc == 0


def outdated(rs):
    cmds = {
        "uv": lambda n: ["uv", "pip", "index", "versions", n],
        "cargo": lambda n: ["cargo", "search", n, "--limit", "1"],
        "npm": lambda n: ["npm", "view", n, "version"],
        "apt": lambda n: ["apt-cache", "policy", n],
    }
    for n, v, k in rs:
        rc, out = run(cmds[k](n), 60)
        m = re.search(r"Candidate: (\S+)" if k == "apt" else r"\d+\.\d+(?:\.\d+)?", out) if rc == 0 else None
        print(
            f"{n} pinned {v} latest {m.group(m.lastindex or 0) if m else '?'}"
            + ("  UPDATE: sec-auditor must vet it first" if m and m.group(m.lastindex or 0) != v else "")
        )


def split_needed(bad, needed_text):
    """-> (really failed, warnings). A tool that did not install only fails the check when the project's AUDIT.md names it; `bad` items are `name version`."""
    need = (needed_text or "").lower()
    return [b for b in bad if b.split()[0].lower() in need], [b for b in bad if b.split()[0].lower() not in need]


def audit_tools_note(path):
    """A warning line when the project's AUDIT.md is missing or has no `audit-tools` block (auditors then run without measurements), plus a starter block drafted from the repo's marker files. "" otherwise."""
    if os.path.exists(path) and "```audit-tools" in read_text(path):
        return ""
    from .starter_tools import draft

    text = draft(os.path.dirname(os.path.abspath(path)))
    proposal = f"proposed starter block for {path} (a draft: the tools still need the TOOL-REQUEST vetting):\n{text}" if text else ""
    return f"warning: no audit-tools block in {path}: auditors run without measurements (add the block to AUDIT.md, practices/audits.md)\n{proposal}"


def _verdict_text(ok, done, bad, warn, note=""):
    return (
        f"VERDICT: {'FAIL' if bad else 'DONE'}\nok: {', '.join(ok) or '-'}\ninstalled: {', '.join(done) or '-'}\nfailed: {', '.join(bad) or '-'}\n"
        + (f"warning (not used by this project, install failed): {', '.join(warn)}\n" if warn else "")
        + note
    )


def _parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    c = s.add_parser("check")
    c.add_argument("--install", action="store_true")
    c.add_argument("--kinds", help="comma list (uv,cargo,npm,apt): only these rows; the entrypoint passes apt, the one kind that needs root")
    c.add_argument("--report")
    c.add_argument(
        "--needed-by", help="AUDIT.md of the project: a tool that failed to install only fails the check when this file names it (others are warnings)"
    )
    c.add_argument("--proj", help="project dir: also (re)create the /opt/jstools/proj eslint/knip scaffold for it")
    c.add_argument("--browser", action="store_true", help="UI stage: also require the chrome-devtools MCP server (fails, never skips)")
    s.add_parser("outdated")
    return p


def main(argv=None):
    a = _parser().parse_args(argv)
    rs = rows(read_text(TABLE))
    if a.cmd == "outdated":
        return outdated(rs)
    if a.kinds:
        rs = [r for r in rs if r[2] in a.kinds.split(",")]
    ok, done, bad = check(rs, a.install)
    warn, note = [], ""
    if a.needed_by is not None:
        bad, warn = split_needed(bad, read_text(a.needed_by) if os.path.exists(a.needed_by) else "")
        note = audit_tools_note(a.needed_by)
    if a.proj:
        ensure_proj(a.proj)
        with contextlib.suppress(ValueError):
            note += "".join(f"warning: ci-drift: {w}\n" for w in drift(a.proj, project(a.proj)))
    if a.browser:
        ensure_browser_user()
        (ok if browser_ok() else bad).append("chrome-devtools MCP")
    text = _verdict_text(ok, done, bad, warn, note)
    if a.report:
        write_text(a.report, text)
    print(text, end="")
    raise SystemExit(1 if bad else 0)
