"""Parsers for audit tools. Each parser: parse(out, err) -> (findings, summary).

A finding is {file, line, rule, severity, message}; the runner adds `tool`.
Coverage parsers return no findings, only a summary.
"""

import csv
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from pipeline.util import parse_jsonl, scan_json


def F(file="", line=0, rule="", severity="warning", message=""):
    return {"file": file or "", "line": int(line or 0), "rule": rule or "", "severity": (severity or "warning").lower(), "message": (message or "").strip()}


def json_doc(text):
    """Parse the first JSON document in text (skips leading log noise)."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:  # KEEP: not a plain JSON document (log noise first); scan_json finds the document
        pass
    found, doc = scan_json(text, "[{", line_start=True)
    if found:
        return doc
    raise ValueError("no JSON document in output")


def json_lines(text):
    return parse_jsonl([ln for ln in text.splitlines() if ln.strip().startswith("{")])[0]


def clippy(out, err):
    res = []
    for d in json_lines(out):
        m = d.get("message")
        if d.get("reason") != "compiler-message" or not m or m.get("level") not in ("warning", "error"):
            continue
        sp: dict = next((s for s in m.get("spans", []) if s.get("is_primary")), {})
        code = (m.get("code") or {}).get("code", "")
        res.append(F(sp.get("file_name"), sp.get("line_start"), code, m["level"], m.get("message")))
    return res, ""


def cargo_audit(out, err):
    d = json_doc(out)
    res = []
    for v in d.get("vulnerabilities", {}).get("list", []):
        a, p = v.get("advisory", {}), v.get("package", {})
        res.append(F("Cargo.lock", 0, a.get("id"), "high", "{} {}: {}".format(p.get("name"), p.get("version"), a.get("title"))))
    for kind, items in d.get("warnings", {}).items():
        for w in items:
            a, p = w.get("advisory") or {}, w.get("package", {})
            res.append(F("Cargo.lock", 0, a.get("id") or kind, "warning", "{} {}: {}".format(p.get("name"), p.get("version"), a.get("title") or kind)))
    return res, ""


def cargo_deny(out, err):
    res = []
    for d in json_lines(out + "\n" + err):
        f = d.get("fields")
        if d.get("type") != "diagnostic" or not f or f.get("severity") not in ("error", "warning"):
            continue
        lab = (f.get("labels") or [{}])[0]
        res.append(F("deny.toml" if not lab.get("span") else lab.get("span"), lab.get("line", 0), f.get("code"), f["severity"], f.get("message")))
    return res, ""


def machete(out, err):
    res, crate = [], ""
    for line in (out + "\n" + err).splitlines():
        m = re.match(r"^(\S+) -- (\S+?):?$", line)
        if m:
            crate = m.group(2)
        elif line.startswith("\t") and crate:
            res.append(F(crate, 0, "unused-dependency", "warning", "unused dependency " + line.strip()))
    return res, ""


def llvm_cov(out, err):
    t = json_doc(out)["data"][0]["totals"]
    return [], "lines {:.1f}% functions {:.1f}% regions {:.1f}%".format(t["lines"]["percent"], t["functions"]["percent"], t["regions"]["percent"])


def knip(out, err):
    d = json_doc(out)
    res = [F(f, 0, "unused-file", "warning", "unused file") for f in d.get("files", [])]
    for it in d.get("issues", []):
        for kind, items in it.items():
            if kind == "file" or not isinstance(items, list):
                continue
            for x in items:
                res.append(F(it.get("file"), x.get("line", 0), "unused-" + kind, "warning", "{} {}".format(kind, x.get("name", ""))))
    return res, ""


def jscpd(out, err):
    d = json_doc(out)
    res = []
    for x in d.get("duplicates", []):
        a, b = x["firstFile"], x["secondFile"]
        res.append(F(a["name"], a.get("start", 0), "duplicate", "warning", f"{int(x.get('lines', 0))} lines duplicated in {b['name']}"))
    pct = d.get("statistics", {}).get("total", {}).get("percentage")
    return res, "" if pct is None else f"duplication {pct}%"


def madge(out, err):
    return [F(c[0], 0, "circular", "error", " -> ".join(c)) for c in json_doc(out) if c], ""


def eslint(out, err):
    res = []
    for f in json_doc(out):
        for m in f.get("messages", []):
            res.append(F(f.get("filePath"), m.get("line"), m.get("ruleId") or "parse", "error" if m.get("severity") == 2 else "warning", m.get("message")))
    return res, ""


def bandit(out, err):
    return [
        F(r["filename"], r.get("line_number"), r.get("test_id"), r.get("issue_severity"), r.get("issue_text")) for r in json_doc(out).get("results", [])
    ], ""


def pip_audit(out, err):
    d = json_doc(out)
    deps = d.get("dependencies", d) if isinstance(d, dict) else d
    return [
        F("requirements", 0, v.get("id"), "high", "{} {}: {}".format(p["name"], p.get("version"), (v.get("description") or "")[:100]))
        for p in deps
        for v in p.get("vulns", [])
    ], ""


def vulture(out, err):
    res = []
    for line in out.splitlines():
        m = re.match(r"^(.+?):(\d+): (.*?) \((\d+)% confidence\)", line)
        if m:
            res.append(F(m.group(1), m.group(2), "unused-code", "warning", m.group(3)))
    return res, ""


def ruff(out, err):
    return [F(r.get("filename"), (r.get("location") or {}).get("row"), r.get("code"), "warning", r.get("message")) for r in json_doc(out)], ""


def npm_audit(out, err):
    res = []
    for name, v in (json_doc(out).get("vulnerabilities") or {}).items():
        via = next((x for x in v.get("via", []) if isinstance(x, dict)), {})
        res.append(F("package.json", 0, via.get("source") or name, v.get("severity"), f"{name}: {via.get('title') or 'vulnerable dependency'}"))
    return res, ""


def cppcheck(out, err):
    """Lines `file:line:severity:id:message` from the --template of the starter command; cppcheck prints them on stderr."""
    res = []
    for line in (out + "\n" + err).splitlines():
        m = re.match(r"^(.+?):(\d+):(\w+):([\w.-]+):(.*)$", line)
        if m:
            res.append(F(m[1], m[2], m[4], "error" if m[3] == "error" else "warning", m[5]))
    return res, ""


def clang_tidy(out, err):
    """Lines `file:line:col: warning: message [check]`; needs a compile_commands.json (the -p dir)."""
    res = []
    for line in out.splitlines():
        m = re.match(r"^(.+?):(\d+):\d+: (warning|error): (.*) \[([\w.,-]+)\]$", line)
        if m:
            res.append(F(m[1], m[2], m[5], m[3], m[4]))
    return res, ""


def lizard(out, err):
    res = []
    for line in out.splitlines():
        m = re.match(r"^(.+?):(\d+): warning: (.*)$", line)
        if m:
            res.append(F(m.group(1), m.group(2), "complexity", "warning", m.group(3)))
            continue
        if line.count(",") >= 10 and not line.startswith("NLOC"):
            row = next(csv.reader(io.StringIO(line)), [])
            if len(row) >= 11 and row[1].isdigit():
                res.append(F(row[6], row[9], "complexity", "info", f"{row[7]} ccn={row[1]} nloc={row[0]}"))
    return res, ""


def gitleaks(out, err):
    d = json_doc(out) if out.strip() else []
    return [F(r.get("File"), r.get("StartLine"), r.get("RuleID"), "high", r.get("Description")) for r in d], ""


def node_cov(out, err):
    for line in out.splitlines():
        m = re.match(r"^[ℹ#]?\s*all files\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)", line)
        if m:
            return [], "line {}% branch {}% funcs {}%".format(*m.groups())
    raise ValueError('no "all files" row in coverage output')


PARSERS = {
    "clippy": clippy,
    "cargo-audit": cargo_audit,
    "cargo-deny": cargo_deny,
    "cargo-machete": machete,
    "cargo-llvm-cov": llvm_cov,
    "knip": knip,
    "jscpd": jscpd,
    "madge": madge,
    "eslint": eslint,
    "bandit": bandit,
    "pip-audit": pip_audit,
    "vulture": vulture,
    "lizard": lizard,
    "ruff": ruff,
    "npm-audit": npm_audit,
    "cppcheck": cppcheck,
    "clang-tidy": clang_tidy,
    "gitleaks": gitleaks,
    "node-coverage": node_cov,
}
