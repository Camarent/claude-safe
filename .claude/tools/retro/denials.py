"""Permission denials of headless Bash calls, counted from Claude session transcripts.

    python3 -I .claude/tools/retro/denials.py [--days N] [--projects DIR] [--examples K] [--json]

A script, no model. Reads `<projects>/<project>/*.jsonl`, skips sessions with entrypoint `cli` (interactive), keeps the rest
(`sdk-cli` = headless). A denial is a Bash tool_result with is_error whose text matches approval|hook|permission|blocked.
Role is not in the transcript, so there is no --by-role.
"""

import argparse
import collections
import datetime
import json
import os
import re
import sys

DENY_RE = re.compile(r"approval|hook|permission|blocked", re.I)
ENV_RE = re.compile(r"^\w+=\S*\s")
SNIP = '"/usr/local/bin/snip" run -- '
APPROVAL = "approval: "


def default_projects():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    home = os.environ.get("HOME")
    cands = [os.path.join(cfg, "projects")] if cfg else []
    if home:
        cands.append(os.path.join(home, ".claude", "projects"))
    cands.append(os.path.join(here, "projects"))
    return next((c for c in cands if os.path.isdir(c)), cands[-1])


def multi_family(text):
    part = text.split(APPROVAL, 1)[1].strip().replace(SNIP, "")
    words = part.split()
    if part.startswith("cd "):
        return "multi: cd&&git" if " git " in f" {part} " else "multi: cd&&other"
    if words and words[0] == "git":
        return "multi: git part"
    return f"multi: other ({' '.join(words[:2])})"


def approval_family(cmd):
    cmd = cmd.strip()
    env = bool(ENV_RE.match(cmd))
    words = ENV_RE.sub("", cmd).split() if env else cmd.split()
    head = words[0] if words else ""
    return "approval: ENV-prefix " + head if env else "approval: " + head


SIMPLE = (
    ("No pipe", "qrun-chain hook"),
    ("sed command", "sed"),
    ("Reviewer sessions may", "reviewer guard"),
    ("redirection", "cd+redirect"),
)


def family(text, cmd=""):
    if "before running a version-control" in text:
        return "cd-then-git"
    if "multiple operations" in text and APPROVAL in text:
        return multi_family(text)
    if text.startswith("This command requires approval"):
        return approval_family(cmd)
    for key, label in SIMPLE:
        if key in text:
            return label
    return "other: " + text[:45]


MULTI_FIX = "one command per Bash call; qrun form: cs-qrun -- <cmd> && <read-only cmd>"
CLASSIFIER_FIX = "split the command; run reads separately"


def fix_for(label):
    """-> the allowed form for a family label, or "" when no fix is known (the harness texts have no hook to change)."""
    if label.startswith("multi: "):
        return MULTI_FIX
    if label.startswith("other: ") and "classifier" in label.lower():
        return CLASSIFIER_FIX
    return ""


def result_text(block):
    c = block.get("content")
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def read_events(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                yield ev


def blocks(ev):
    msg = ev.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _note_block(b, day, cmds, calls, denials):
    """Record one content block: a Bash call (into cmds and calls) or a denied result (into denials)."""
    if b.get("type") == "tool_use" and b.get("name") == "Bash":
        cmds[b.get("id")] = (b.get("input") or {}).get("command", "")
        calls.append(day)
    elif b.get("type") == "tool_result" and b.get("is_error") and b.get("tool_use_id") in cmds:
        text = result_text(b)
        if DENY_RE.search(text):
            denials.append((day, text, cmds[b["tool_use_id"]]))


def scan_file(path, cutoff):
    """Return (call days, denials) of one transcript; a denial is (day, text, cmd)."""
    events = list(read_events(path))
    if any(e.get("entrypoint") == "cli" for e in events):
        return [], []
    cmds: dict[str, str] = {}
    calls: list[str] = []
    denials: list[tuple[str, str, str]] = []
    for ev in events:
        day = str(ev.get("timestamp", ""))[:10]
        if day < cutoff:
            continue
        for b in blocks(ev):
            _note_block(b, day, cmds, calls, denials)
    return calls, denials


def collect(projects, days, today=None):
    today = today or datetime.date.today()
    cutoff = (today - datetime.timedelta(days=days)).isoformat()
    calls, denials = [], []
    for proj in sorted(os.listdir(projects)) if os.path.isdir(projects) else []:
        pdir = os.path.join(projects, proj)
        for name in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
            if name.endswith(".jsonl"):
                c, d = scan_file(os.path.join(pdir, name), cutoff)
                calls += c
                denials += d
    return calls, denials


def summarize(calls, denials):
    fams: dict[str, dict] = {}
    for _day, text, cmd in denials:
        e = fams.setdefault(family(text, cmd), {"count": 0, "example": cmd})
        e["count"] += 1
    ordered = sorted(fams.items(), key=lambda kv: -kv[1]["count"])
    per_day: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for d in calls:
        per_day[d][0] += 1
    for d, _t, _c in denials:
        per_day[d][1] += 1
    n, k = len(calls), len(denials)
    return {
        "bash_calls": n,
        "denied": k,
        "rate": k / n if n else 0.0,
        "per_day": {d: {"calls": v[0], "denied": v[1], "rate": v[1] / v[0] if v[0] else 0.0} for d, v in sorted(per_day.items())},
        "families": [{"family": f, "count": e["count"], "share": e["count"] / k, "example": e["example"]} for f, e in ordered],
    }


def render(s, examples):
    out = [f"Bash calls: {s['bash_calls']}  denied: {s['denied']}  rate: {100 * s['rate']:.1f}%", "Per day:"]
    out += [f"  {d}  {v['denied']}/{v['calls']}  {100 * v['rate']:.1f}%" for d, v in s["per_day"].items()]
    out.append("Families:")
    for f in s["families"]:
        out.append(f"  {f['count']:5d}  {100 * f['share']:5.1f}%  {f['family']}")
        if fix_for(f["family"]):
            out.append("          fix: " + fix_for(f["family"]))
        if examples:
            out.append("          e.g. " + f["example"][:160].replace("\n", " "))
    return "\n".join(out)


def log_rows(path):
    """Rows of a denials.jsonl (denial_log.py: one {ts, rule, command, cwd} object per line); a missing file has none."""
    if not os.path.isfile(path):
        return []
    return [ev for ev in read_events(path) if "rule" in ev]


def by_rule(rows):
    """-> [{agent, rule, count}] of the log rows, most denials first; rows without an agent (older logs) count under agent ""."""
    counts = collections.Counter((str(r.get("agent", "")), str(r["rule"])) for r in rows)
    return [{"agent": agent, "rule": rule, "count": n} for (agent, rule), n in counts.most_common()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--projects", default=None)
    ap.add_argument("--examples", type=int, default=1, help="show one example command per family when > 0")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", default=None, help="count the rows of this denials.jsonl by rule instead of reading transcripts")
    a = ap.parse_args(argv)
    if a.log:
        rules = by_rule(log_rows(a.log))
        if a.json:
            print(json.dumps(rules, indent=1))
        else:
            print("\n".join(f"  {r['count']:5d}  {r['agent'] or '-'}  {r['rule']}" for r in rules) or "no denials logged")
        return 0
    calls, denials = collect(a.projects or default_projects(), a.days)
    s = summarize(calls, denials)
    print(json.dumps(s, indent=1) if a.json else render(s, a.examples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
