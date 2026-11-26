#!/usr/bin/env python3
"""incidents: find suspected reviewer misses of a finished stage and keep the confirmed ones as future reviewer-eval cases.

scan  --stage S [--dir D]   Reads D/.work/S/orch.jsonl and the task patches `wt.py merge` saved in D/.work/S/patches/. A trigger (a red final-test,
                            a merge-fix round, a blocked pr-ready) is matched to the tasks whose patch touches a file named in the failure text.
                            Writes D/.work/S/retro/incidents.json and prints one INCIDENT line per trigger. Suspicion only: no model, no verdict.
keep  --stage S --id I --label reviewer-miss|not-a-miss|unclear --cause "one line" [--task T] [--dir D]
                            The lead's label after reading the stub. Writes D/.notes/incidents/<stage>-<I>.md; for `reviewer-miss` also the patch
                            and the task spec next to it, so a test can rebuild the reviewed diff. Project data: never copy it into the shared pipeline.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from pipeline import gitx
from pipeline.configuration import ORCH_LOG, RETRO_DIR, SLOW_SUITE_FILE, SLOW_SUITE_KIND, WORK_DIR
from pipeline.util import read_jsonl, work_path

from retro.feature_retro import read

TRIGGERS = (("final-test", "FAIL"), ("merge-fix", None), ("pr-ready", "BLOCKED"))
LABELS = ("reviewer-miss", "not-a-miss", "unclear")
MAX_FAILURE = 1500


def _events(path):
    try:
        return read_jsonl(path, quiet=True, errors="replace")
    except OSError:  # unreadable log: no events, as before
        return []


_HEADER_RE = re.compile(r'^diff --git (?:a/(.+) b/\1|"a/(.+)" "b/\2")$', re.M)


def _patch_files(text):
    """Files named in `diff --git` headers; spaces work (same name both sides), quoted non-ASCII names are decoded."""
    return [gitx.unquote_path(m[2]) if m[2] else m[1] for m in _HEADER_RE.finditer(text)]


def _reviewer_verdicts(evs, task):
    return [e.get("verdict") for e in evs if e.get("ev") == "report" and e.get("role") == "reviewer" and e.get("task") == task]


def _is_trigger(e):
    if e.get("ev") != "report" or not e.get("task"):
        return False
    t = str(e["task"])
    return any(t.startswith(name) and (verdict is None or e.get("verdict") == verdict) for name, verdict in TRIGGERS)


def _slow_row_ok(r):
    """A slow-suite row from slow_suite.record: kind, project, step, and numeric seconds and limit."""
    nums = all(isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool) for k in ("seconds", "limit"))
    return r.get("kind") == SLOW_SUITE_KIND and nums and isinstance(r.get("project"), str) and isinstance(r.get("step"), str)


def _slow_stub(r):
    """A slow-suite row -> an incident stub with no candidates (no patch matches a timed-out command)."""
    text = f"{r['project']} {r['step']} {r['seconds']}s over limit {r['limit']}s"
    return {
        "trigger": SLOW_SUITE_KIND,
        "project": r["project"],
        "step": r["step"],
        "seconds": r["seconds"],
        "limit": r["limit"],
        "failure": text,
        "candidates": [],
    }


def scan(d, stage):
    """-> list of incident stubs; also written to .work/<stage>/retro/incidents.json. Slow-suite stubs come first."""
    base = os.path.join(d, WORK_DIR, stage)
    evs = _events(os.path.join(base, ORCH_LOG))
    pdir = os.path.join(base, "patches")
    patches = {}
    if os.path.isdir(pdir):
        for name in sorted(os.listdir(pdir)):
            if name.endswith(".patch"):
                patches[name] = read(os.path.join(pdir, name))
    slow = [_slow_stub(r) for r in _events(os.path.join(base, RETRO_DIR, SLOW_SUITE_FILE)) if _slow_row_ok(r)]
    trig = []
    for e in evs:
        if not _is_trigger(e):
            continue
        text = (read(e.get("file", "")) or str(e.get("why") or "")).strip()
        cands = []
        for name, body in patches.items():
            hit = [f for f in _patch_files(body) if f in text]
            if hit:
                task = name[: -len(".patch")].split("__")[0]
                cands.append({"task": task, "patch": name, "files_hit": hit[:8], "reviewer": _reviewer_verdicts(evs, task)})
        trig.append(
            {
                "trigger": f"{e['task']} {e.get('verdict', '')}".strip(),
                "event": e.get("id"),
                "failure": text[-MAX_FAILURE:],
                "candidates": cands,
            }
        )
    stubs = slow + trig
    for n, s in enumerate(stubs, 1):
        s["id"] = f"i{n}"
    os.makedirs(base, exist_ok=True)
    with open(work_path(base, "incidents.json", create=True), "w", encoding="utf-8") as f:
        json.dump(stubs, f, indent=1)
    return stubs


def render(stubs, stage):
    if not stubs:
        return ["INCIDENTS: none (no red final-test, merge-fix or blocked pr-ready)"]
    out = [
        f'INCIDENTS: {len(stubs)} suspected; read each, then label with `retro/incidents.py keep --stage {stage} --id <id> --label reviewer-miss|not-a-miss|unclear --cause ".."`'
    ]
    for s in stubs:
        if s["trigger"] == SLOW_SUITE_KIND:
            out.append(
                f"  {s['id']}: {SLOW_SUITE_KIND} {s['project']} {s['step']} {s['seconds']}s over limit {s['limit']}s: raise tool_settings.script_timeout_s or skip with --note"
            )
            continue
        who = (
            ", ".join(f"{c['task']} (review: {'/'.join(map(str, c['reviewer'])) or 'none'})" for c in s["candidates"])
            or "no task patch matches the failure text"
        )
        out.append(f"  {s['id']}: {s['trigger']} -> {who}")
    return out


def keep(d, stage, iid, label, cause, task=""):
    """Write the labelled incident into .notes/incidents/. -> the md path."""
    if label not in LABELS:
        raise ValueError(f"label must be one of {', '.join(LABELS)}")
    base = os.path.join(d, WORK_DIR, stage)
    with open(work_path(base, "incidents.json"), encoding="utf-8") as f:
        stub = next((s for s in json.load(f) if s["id"] == iid), None)
    if stub is None:
        raise ValueError(f"no incident {iid} in {base}/retro/incidents.json")
    cand = next((c for c in stub["candidates"] if not task or c["task"] == task), None)
    out = os.path.join(d, ".notes", "incidents")
    os.makedirs(out, exist_ok=True)
    key = f"{stage}-{iid}"
    lines = [f"# incident {key} ({label})", "", f"cause: {cause}", f"trigger: {stub['trigger']}", f"task: {cand['task'] if cand else 'unknown'}"]
    if cand:
        lines.append(f"reviewer verdicts: {'/'.join(map(str, cand['reviewer'])) or 'none'}")
    lines += ["", "failure:", "", stub["failure"], ""]
    if label == "reviewer-miss" and cand:
        patch = read(os.path.join(base, "patches", cand["patch"]))
        spec = read(os.path.join(base, f"{cand['task']}.md"))
        with open(os.path.join(out, f"{key}.patch"), "w", encoding="utf-8") as f:
            f.write(patch)
        lines += [f"patch: {key}.patch (the diff the reviewer saw)", "", "task spec:", "", spec]
    path = os.path.join(out, f"{key}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--stage", required=True)
    s.add_argument("--dir", default=".")
    k = sub.add_parser("keep")
    k.add_argument("--stage", required=True)
    k.add_argument("--id", required=True)
    k.add_argument("--label", required=True, choices=LABELS)
    k.add_argument("--cause", required=True)
    k.add_argument("--task", default="")
    k.add_argument("--dir", default=".")
    a = ap.parse_args(argv)
    d = os.path.abspath(a.dir)
    if a.cmd == "scan":
        print("\n".join(render(scan(d, a.stage), a.stage)))
    else:
        print(keep(d, a.stage, a.id, a.label, a.cause, a.task))
    return 0


if __name__ == "__main__":
    sys.exit(main())
