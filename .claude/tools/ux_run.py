"""Script step before ux-reviewer: build and serve the app from the ```ux block of AUDIT.md, run ux_shots.js, stop the server.

    python3 -I /root/.claude/tools/ux_run.py --stage S [--dir .] [--var name=value ...] [--out .work/S/ux-shots]

AUDIT.md block (project specific; `<name>` is replaced from --var or a `UX-VARS: name=value ...` line in TASKS.md):
    ```ux
    build: <command>        (optional)
    serve: <command>        (optional, started in the background)
    url: <url>
    ```
First output line `VERDICT: DONE|FAIL`. pipeline.json `ux_flows` (a steps file under the repo) is passed as --steps, else the tool walks the first 8 buttons. ux-reviewer reads <out>/SUMMARY.md and may extend ux_shots.js or pass its own --steps file.
"""

import argparse
import contextlib
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir

from pipeline.configuration import LONG_CMD_TIMEOUT_S, VERDICT_PREFIX, WORK_DIR, project
from pipeline.ux_flows import flows_file, flows_rel, path_problem, sub, variables
from pipeline.ux_flows import read as read_flows

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_TIMEOUT_S = float(os.environ.get("ORCH_SCRIPT_TIMEOUT", 900))


def block(text):
    m = re.search(r"^```ux\s*\n(.*?)^```", text, re.M | re.S)
    pairs = (line.partition(":") for line in (m.group(1).splitlines() if m else []) if ":" in line)
    return {k.strip(): x.strip() for k, _, x in pairs}


def up(url):
    try:
        urllib.request.urlopen(url, timeout=2)  # nosec B310  # http URL set by the operator, not user input
        return True
    except urllib.error.HTTPError:  # any HTTP answer (404, 401...) means the server is up
        return True
    except OSError:
        return False


def _parse_args(argv):
    """The ux_run command line."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--dir", default=".")
    ap.add_argument("--var", action="append", default=[])
    ap.add_argument("--out")
    ap.add_argument("--repo", help="repo that holds AUDIT.md and is built/served (default: --dir); stage files stay under --dir")
    return ap.parse_args(argv)


def _read(base, p):
    """Text of file P under BASE, or '' when it cannot be read."""
    try:
        return Path(os.path.join(base, p)).read_text(encoding="utf-8")
    except OSError:
        return ""


def _fail(text):
    """Print the FAIL verdict with its reason; -> exit code 1."""
    print(VERDICT_PREFIX + "FAIL\n" + text)
    return 1


def _ux_block(repo, d, stage, given):
    """The ```ux block of AUDIT.md with its placeholders filled from TASKS.md and the --var values."""
    b = block(_read(repo, project(repo)["audit_file"]))
    v = variables(_read(d, f".work/{stage}/TASKS.md"), given)
    return {k: sub(x, v) for k, x in b.items()}


def _block_error(b):
    """Why the ux block cannot run (no url, or a placeholder left unfilled); '' when it can."""
    if "url" not in b:
        return "no ```ux block with a url in AUDIT.md"
    if "<" in "".join(b.values()):
        return (
            "unfilled placeholder in the ux block: "
            + ", ".join(sorted(set(re.findall(r"<\w+>", "".join(b.values())))))
            + " (UX-VARS line in TASKS.md or --var)"
        )
    return ""


def _build(b, repo):
    """Run the build command of the ux block; -> error text, or '' when it passed or none is set."""
    if not b.get("build"):
        return ""
    try:
        r = subprocess.run(shlex.split(b["build"]), cwd=repo, capture_output=True, text=True, timeout=BUILD_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return f"build timed out after {BUILD_TIMEOUT_S:g}s: {b['build']}"
    except OSError as e:
        return f"build could not start: {e}"
    if r.returncode:
        return "build failed: " + (r.stdout + r.stderr).strip()[-600:]
    return ""


def _wait_up(url):
    """True once URL answers, polled once a second for 30 seconds."""
    for _ in range(30):
        if up(url):
            return True
        time.sleep(1)
    return False


def _serve_and_shoot(b, repo, d, out, steps=""):
    """Start the serve command, wait for its url, run ux_shots.js against it and stop the server; -> exit code."""
    srv = (
        subprocess.Popen(shlex.split(b["serve"]), cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if b.get("serve")
        else None
    )
    try:
        if not _wait_up(b["url"]):
            return _fail("server not reachable at " + b["url"])
        r = subprocess.run(
            ["node", os.path.join(HERE, "ux_shots.js"), "--url", b["url"], "--out", out, *(["--steps", steps] if steps else [])],
            capture_output=True,
            text=True,
            timeout=LONG_CMD_TIMEOUT_S,
            check=False,
        )
        if r.returncode:
            return _fail("ux_shots failed: " + (r.stdout + r.stderr).strip()[-600:])
        print(f"{VERDICT_PREFIX}DONE\nshots and SUMMARY.md in {os.path.relpath(out, d)}; url {b['url']}")
        return 0
    finally:
        if srv:
            with contextlib.suppress(ProcessLookupError):  # server already exited
                os.killpg(srv.pid, signal.SIGTERM)


def main(argv=None):
    a = _parse_args(argv)
    d = os.path.abspath(a.dir)
    repo = os.path.abspath(a.repo or a.dir)
    out = os.path.abspath(a.out or os.path.join(d, WORK_DIR, a.stage, "ux-shots"))
    b = _ux_block(repo, d, a.stage, a.var)
    err = _block_error(b)
    if err:
        return _fail(err)
    err = _build(b, repo)
    if err:
        return _fail(err)
    vs = variables(_read(d, f".work/{a.stage}/TASKS.md"), a.var)
    cfg = project(d)
    steps = flows_file(repo, cfg, vs)  # the project's named flows replace the blind walk over the first buttons
    if steps:
        bad = path_problem(flows_rel(cfg, vs))
        _, probs = ([], [bad]) if bad else read_flows(steps)
        if probs:
            return _fail("ux_flows: " + "; ".join(probs))
    return _serve_and_shoot(b, repo, d, out, steps)


if __name__ == "__main__":
    sys.exit(main())
