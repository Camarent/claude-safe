"""Classify code-search tool calls in a Claude transcript: local.py leads vs plain grep/glob.

CLI (any transcript, e.g. the lead's): python3 -I lp_share.py <session.jsonl>...  -> counts and the local.py share."""

import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from pipeline.configuration import SEARCH_TOOL_NAMES
from pipeline.shellwords import split_commands, strip_heredocs

TOOL_RE = "(?:" + "|".join(rf"(?:^|[\s;|&(/]){re.escape(n)}" for n in SEARCH_TOOL_NAMES) + ")"  # local search tool by any of its names
LOCAL_RE = re.compile(TOOL_RE + r"\s+(find|nav\s+(?:def|refs|symbols|outline))\b")
GREP_WORD = {"grep", "egrep", "fgrep", "rg", "ugrep", "ag"}
PIPELINE_RE = re.compile(r"/root/\.claude")
LOCAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "search", "local.py")
NONCODE_RE = re.compile(r"\.(md|json|jsonl|log|txt|csv)\b|\.work/|\.notes/|\.audits/|/tmp/|tool-results|/scratchpad|\.git/")


FILE_RE = re.compile(r"[^*?\[\]{}\s]+\.\w{1,6}$")  # one named file: no wildcard, has an extension


def _known_file(path):
    """True when `path` names one file: the location was already known, so a grep in it is no search for an unknown location."""
    return bool(path) and not path.endswith("/") and bool(FILE_RE.fullmatch(str(path).strip("'\"")))


def _bash_kind(cmd):
    """-> 'local:...' | 'grep' | None; grep counts only as a command word in project code, not heredoc text, pipe filter or docs."""
    cmd = strip_heredocs(cmd, keep_opener=False)
    for seg in split_commands(cmd, pipe=False, amp=False):  # a pipe is not a split: `x | grep` filters output, it is no search
        if PIPELINE_RE.search(seg.replace(LOCAL_PATH, "")):
            continue
        m = LOCAL_RE.search(seg)
        if m:
            return "local:" + m.group(1).replace(" ", "-")
        words = seg.split()
        while words and (words[0] in ("cd", "sudo", "time") or re.match(r"\w+=", words[0])):
            words = words[2:] if words[0] == "cd" else words[1:]
        if words and words[0] in GREP_WORD and not NONCODE_RE.search(seg):
            args = [w for w in words[1:] if not w.startswith("-")]
            if len(args) >= 2 and _known_file(args[-1]):
                continue  # pattern + one named file: known location
            return "grep"
    return None


def classify(name, inp) -> str | None:
    """-> 'local:find' | 'local:nav-<sub>' | 'grep' | 'glob' | None for one tool_use block.
    Calls aimed at the pipeline itself (the pipeline HOME) or at docs/logs are not project code search: None."""
    inp = inp or {}
    if name != "Bash" and PIPELINE_RE.search(json.dumps(inp)):
        return None
    if name in ("Grep", "Glob"):
        if NONCODE_RE.search(str(inp.get("path", "")) + " " + str(inp.get("glob", "")) + " " + str(inp.get("pattern", "")) * (name == "Glob")):
            return None
        if name == "Grep" and _known_file(str(inp.get("path", ""))):
            return None  # known file: not a search for an unknown location
        return name.lower()
    if name == "Bash":
        return _bash_kind(str(inp.get("command", "")))
    return None


def count_file(path, c=None) -> collections.Counter:
    """Add the search-call counts of one transcript file to Counter c."""
    c = collections.Counter() if c is None else c
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    m = (json.loads(line).get("message") or {}).get("content")
                except ValueError:
                    continue
                for b in m if isinstance(m, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        k = classify(b.get("name"), b.get("input"))
                        if k:
                            c[k] += 1
    except OSError:
        pass
    return c


def share(c) -> float:
    """local.py calls / search calls for an UNKNOWN location (local search + grep/glob; a grep in one named file is not counted), 0.0 when none."""
    loc = sum(v for k, v in c.items() if k.startswith("local:"))
    tot = loc + c.get("grep", 0) + c.get("glob", 0)
    return loc / tot if tot else 0.0


if __name__ == "__main__":
    tot: collections.Counter = collections.Counter()
    for p in sys.argv[1:]:
        count_file(p, tot)
    print(dict(tot), "local.py share: %.0f%%" % (100 * share(tot)))
