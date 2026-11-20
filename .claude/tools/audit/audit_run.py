#!/usr/bin/env python3
"""audit_run: run the audit-tools block of <repo>/AUDIT.md, print a quiet summary, write findings.json."""

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx
from pipeline.configuration import GIT_QUICK_TIMEOUT_S, GIT_TIMEOUT_S, project
from pipeline.util import script_timeout

from audit.audit_parsers import PARSERS

BLOCK = re.compile(r"^```audit-tools\s*\n(.*?)^```", re.M | re.S)
MISSING = re.compile(r"command not found|no such command|not found: |No such file or directory", re.I)
REQUIRED_VAR = re.compile(r"\$\{(\w+):\?")  # `${VAR:?msg}` in a tool command: the variable must be set


def load_tools(repo):
    """Return [(name, cwd, command, parser)] from the audit-tools block."""
    with open(os.path.join(repo, project(repo)["audit_file"]), encoding="utf-8") as fh:
        m = BLOCK.search(fh.read())
    tools = []
    for line in m.group(1).splitlines() if m else []:
        if line.strip() and not line.lstrip().startswith("#"):
            p = [x.strip() for x in line.split("|")]
            if len(p) < 4:
                raise SystemExit("bad audit-tools line: " + line)
            tools.append((p[0], p[1], "|".join(p[2:-1]), p[-1]))
    return tools


def key(f):
    """Stable identity of a finding: tool+file+rule+message, never the line."""
    return hashlib.sha1("\x00".join((f["tool"], f["file"], f["rule"], f["message"])).encode(), usedforsecurity=False).hexdigest()[:16]


def diff(cur, base):
    """Return {new, fixed, unchanged} counts of cur vs base finding lists."""
    a = collections.Counter(key(f) for f in cur)
    b = collections.Counter(key(f) for f in base)
    return {"new": sum((a - b).values()), "fixed": sum((b - a).values()), "unchanged": sum((a & b).values())}


def _mirror(repo, proj):
    """A tool dir outside the repo (e.g. /opt/jstools/proj: config plus symlinks `web`, `tools` that point at ONE checkout) is copied to a temp dir
    whose symlinks point at the same-named paths of `repo`, so a job worktree is analysed, not the main checkout. -> (cwd, temp dir to remove)."""
    tmp = tempfile.mkdtemp(prefix="audit-proj-")
    for name in os.listdir(proj):
        src, dst = os.path.join(proj, name), os.path.join(tmp, name)
        if os.path.islink(src):
            mine = os.path.join(repo, name)
            os.symlink(mine if os.path.exists(mine) else os.readlink(src), dst)
        elif os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
    nm = os.path.join(os.path.dirname(proj), "node_modules")
    if os.path.isdir(nm) and not os.path.exists(os.path.join(tmp, "node_modules")):
        os.symlink(nm, os.path.join(tmp, "node_modules"))
    return tmp, tmp


def _timeout():
    """Seconds one audit tool may run: env ORCH_SCRIPT_TIMEOUT, default SCRIPT_TIMEOUT_S (as qrun.py)."""
    return script_timeout()


def run_tool(repo, name, cwd, cmd, parser, rawdir, stamp):
    """Run one tool; return (status, findings, summary) with status ok|findings|missing|unset|crash."""
    if cwd == "/opt/jstools/proj" and not os.path.isdir(cwd) and os.path.isdir("/opt/jstools/node_modules"):
        from pipeline.extra_tools import ensure_proj  # scaffold is lost on container restart: rebuild it

        ensure_proj(repo)
    unset = [v for v in REQUIRED_VAR.findall(cmd) if not os.environ.get(v)]
    if unset:
        return "unset", [], unset[0]
    if not os.path.isdir(os.path.join(repo, cwd)):
        return "missing", [], ""
    orig_cwd = os.path.join(repo, cwd)
    run_cwd, mirror = _mirror(repo, orig_cwd) if os.path.isabs(cwd) else (orig_cwd, None)
    try:
        p = subprocess.run(["bash", "-c", cmd], cwd=run_cwd, capture_output=True, text=True, errors="replace", check=False, timeout=_timeout())
    except subprocess.TimeoutExpired:
        return "crash", [], f"timed out after {_timeout():g}s (ORCH_SCRIPT_TIMEOUT)"
    except OSError as e:
        return "crash", [], f"cannot run: {e}"
    finally:
        if mirror:
            shutil.rmtree(mirror, ignore_errors=True)
    with open(os.path.join(rawdir, f"{stamp}-{name}.log"), "w") as fh:
        fh.write(f"$ {cmd}\n(rc {p.returncode})\n--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}\n")
    return _parsed(p, name, parser, mirror, orig_cwd)


def _parsed(p, name, parser, mirror, orig_cwd):
    """-> (status, findings, summary) from a finished tool process P; paths under the scratch MIRROR are mapped back to ORIG_CWD."""
    if p.returncode == 127 or (p.returncode != 0 and not p.stdout.strip() and MISSING.search(p.stderr)):
        return "missing", [], ""
    fn = PARSERS.get(parser)
    if fn is None:
        tail = (p.stdout + p.stderr).splitlines()[-15:]
        return "findings", [], f"UNKNOWN-PARSER {parser} (rc {p.returncode}); last lines:\n" + "\n".join(tail)
    try:
        fl, summ = fn(p.stdout, p.stderr)
    except Exception as e:  # parser failure means the tool crashed or its format changed
        return "crash", [], f"{type(e).__name__}: {e}"
    for f in fl:
        f["tool"] = name
        if mirror and f.get("file"):
            f["file"] = f["file"].replace(mirror, orig_cwd)  # keep the same paths as a run in the real tool dir (baselines compare them)
    if mirror:
        summ = summ.replace(mirror, orig_cwd)
    return ("findings" if fl else "ok"), fl, summ


def _stamp(repo):
    """Date, plus the short HEAD sha when git can say."""
    stamp = datetime.date.today().isoformat()
    try:
        sha = gitx.run(repo, "rev-parse", "--short", "HEAD", timeout=GIT_QUICK_TIMEOUT_S)
        if sha.returncode == 0 and sha.stdout.strip():
            stamp += "-" + sha.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return stamp


SUPPRESSION = re.compile(
    r"#!?\[(allow|expect)\(|eslint-disable|#\s*noqa|#\s*type:\s*ignore|#\s*nosec|#\s*pragma:\s*no cover|#\s*pragma\s+warning\s*\(\s*disable|//\s*nolint|NOLINT|@ts-ignore|@ts-expect-error|#\s*fmt:\s*(skip|off)|istanbul ignore"
)


def suppressions(repo, base):
    """-> findings (tool `suppression`) for every added line in `git diff base...HEAD` that adds a lint/clippy/type suppression: the review input for "no new allow without a reason"."""
    try:
        p = gitx.run(repo, "-c", "core.quotepath=off", "diff", "-U0", "--no-color", f"{base}...HEAD", timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for file, lines in gitx.added_lines(p.stdout).items():
        for line, text in lines:
            if SUPPRESSION.search("+" + text):
                out.append(
                    {"file": file, "line": line, "rule": "suppression-added", "severity": "warning", "message": text.strip()[:200], "tool": "suppression"}
                )
    return out


def _print_result(name, fl, summ, verbose):
    """Print the summary of one finished tool; -> 1 when it has findings, else 0."""
    if summ.startswith("UNKNOWN-PARSER"):
        print(f"FINDINGS {name} ? {summ}")
        return 1
    if not fl:
        print("OK {} {}".format(name, summ if summ else "clean"))
        return 0
    sev = collections.Counter(f["severity"] for f in fl)
    sev_text = ", ".join(f"{k} {v}" for k, v in sorted(sev.items()))
    print(f"FINDINGS {name} {len(fl)} ({sev_text})")
    for f in fl if verbose else fl[:10]:
        print("  {}:{} {} {}".format(f["file"], f["line"], f["rule"], f["message"][:160]))
    if len(fl) > 10 and not verbose:
        print(f"  ... +{len(fl) - 10} more, see findings.json")
    if summ:
        print("  " + summ)
    return 1


def _parse_args(argv):
    """The audit_run command line."""
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--only")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--baseline")
    ap.add_argument("--base", help="git ref: also list the lint/clippy suppressions added in BASE...HEAD as findings (tool suppression)")
    ap.add_argument("--verdict", action="store_true", help="first output line VERDICT: DONE|FAIL (exit 0 = DONE), for the wave script step")
    return ap.parse_args(argv)


def _selected(tools, only):
    """The tools whose names are in the comma list ONLY; all tools when ONLY is unset."""
    if not only:
        return tools
    want = set(only.split(","))
    return [t for t in tools if t[0] in want]


def _tool_problem(name, st, summ, rawdir, stamp):
    """Print the line of a tool that is missing, unset or crashed; -> exit code 2, or None when the tool ran."""
    if st == "missing":
        print("TOOL-MISSING: " + name)
    elif st == "unset":
        print(f"TOOL-UNSET: {name} needs the environment variable {summ}")
    elif st == "crash":
        print(f"CRASH {name} {summ} (see {rawdir}/{stamp}-{name}.log)")
    else:
        return None
    return 2


def _run_tools(repo, tools, a, stamp, rawdir):
    """Run each tool and print its result; -> (findings, counts per tool, exit code)."""
    allf, counts, code = [], {}, 0
    for name, cwd, cmd, parser in tools:
        st, fl, summ = run_tool(repo, name, cwd, cmd, parser, rawdir, stamp)
        problem = _tool_problem(name, st, summ, rawdir, stamp)
        if problem is not None:
            code = problem
            continue
        counts[name] = len(fl)
        allf += fl
        code = code or _print_result(name, fl, summ, a.verbose)
    return allf, counts, code


def _suppressions(repo, base, counts):
    """Print and count the suppressions added since BASE; -> their findings."""
    sup = suppressions(repo, base)
    counts["suppression"] = len(sup)
    print(f"suppressions added since {base}: {len(sup)}" + "".join(f"\n  {f['file']}:{f['line']}: {f['message']}" for f in sup[:20]))
    return sup


def _baseline(allf, path, out):
    """Diff the findings against the baseline file PATH, store the counts in OUT and print them."""
    with open(path) as fh:
        b = json.load(fh)
    out["baseline"] = diff(allf, b["findings"] if isinstance(b, dict) else b)
    print("BASELINE new {new} fixed {fixed} unchanged {unchanged}".format(**out["baseline"]))


def _write_findings(rawdir, stamp, out, only=None):
    """Write OUT as <stamp>-findings.json in RAWDIR; -> its path. A `--only` run writes <stamp>-only-<tools>.json instead: the consolidated findings of that commit (what the auditors read, `*findings.json`) are never overwritten by a partial run."""
    path = os.path.join(rawdir, f"{stamp}-only-{only.replace(',', '+')}.json" if only else stamp + "-findings.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)
    return path


def main(argv=None):
    a = _parse_args(argv)
    repo = os.path.abspath(a.repo)
    if not os.path.isdir(repo):
        print("ERROR: target is not a directory: " + repo, file=sys.stderr)
        return 2
    if "JSTOOLS" not in os.environ and os.path.isdir("/opt/jstools/node_modules"):
        os.environ["JSTOOLS"] = "/opt/jstools"  # unset var made `${JSTOOLS:?}` fail and read as TOOL-MISSING
    tools = _selected(load_tools(repo), a.only)
    if not tools:
        print("EMPTY: no audit tools selected")
        return 3
    stamp = _stamp(repo)
    rawdir = os.path.join(repo, ".audits", "raw")
    os.makedirs(rawdir, exist_ok=True)
    allf, counts, code = _run_tools(repo, tools, a, stamp, rawdir)
    if a.base:
        allf += _suppressions(repo, a.base, counts)
    out: dict = {"counts": counts, "findings": allf}
    if a.baseline:
        _baseline(allf, a.baseline, out)
    print("findings: " + _write_findings(rawdir, stamp, out, a.only))
    return code


def run(argv=None):
    if "--verdict" not in (argv if argv is not None else sys.argv[1:]):
        return main(argv)
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(argv)
    rc = 0 if rc < 2 else rc  # 1 = findings (input for the auditors), 2 = tool missing or crashed
    print("VERDICT: " + ("DONE" if rc == 0 else "FAIL"))
    print(buf.getvalue(), end="")
    return rc


if __name__ == "__main__":
    sys.exit(run())
