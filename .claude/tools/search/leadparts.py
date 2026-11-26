"""leadparts: pieces of code context from `lp nav`, any language. Shared by plan_context (planner and auditors) and pipeline/steps.leads_for (task briefs)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from codenames import code_name, hit_file

from search.lpcall import lp


def is_test(path):
    """True for a test file: `test_*`, `*_test.*`, `*.test.*`, `*.spec.*` by basename, or a file below tests/ or __tests__/ (not a substring match: latest.py is no test)."""
    p = path.lower()
    b = os.path.basename(p)
    return b.startswith("test_") or "_test." in b or ".test." in b or ".spec." in b or "/tests/" in "/" + p or "/__tests__/" in "/" + p


def marked_names(text, limit=10):
    """Identifiers the text marks as code with backticks, in order, no repeats."""
    names = []
    for tok in text.split("`")[1::2]:
        n = code_name(tok) if 2 <= len(tok) <= 60 else None
        if n and n not in names:
            names.append(n)
    return names[:limit]


def symbol_lines(repo, text, limit=10):
    """`- `name`: where it is defined` for every marked name `lp nav symbols` knows."""
    out = []
    for n in marked_names(text, limit):
        r = [x for x in lp(repo, "nav", "symbols", n).splitlines() if x.strip() and x.strip() != "(none)"]
        if r:
            out.append(f"- `{n}`: " + "; ".join(r[:3]))
    return out


DEF_KINDS = {"function", "method", "class", "struct", "enum", "interface", "trait", "module", "namespace", "type", "constructor", "impl"}


def parse_outline(lines):
    """`lp nav outline` lines `LINE:COL  kind name` -> [(line, kind, name)]; anything that does not parse is skipped."""
    out = []
    for x in lines:
        parts = x.split(None, 2)
        if len(parts) == 3 and parts[0].partition(":")[0].isdigit():
            out.append((int(parts[0].partition(":")[0]), parts[1], parts[2].strip()))
    return out


def definitions(outline, only=None):
    """[(line, name)] of real definitions: no locals, properties, anonymous or callback entries; `only(line)` narrows to some lines."""
    seen, out = set(), []
    for ln, kind, name in parse_outline(outline):
        if kind in DEF_KINDS and "(" not in name and "<" not in name and "callback" not in name and name not in seen and (only is None or only(ln)):
            seen.add(name)
            out.append((ln, name))
    return out


def outline_lines(repo, files, limit=12):
    """One packed line per existing file: `path: name@line name@line ...` (definitions only). Directories, globs and unknown files are skipped."""
    out = []
    for f in files:
        if not os.path.isfile(os.path.join(repo, f)):
            continue
        d = definitions(lp(repo, "nav", "outline", f).splitlines())
        if d:
            out.append(f"{f}: " + " ".join(f"{n}@{ln}" for ln, n in d[:limit]) + (f" +{len(d) - limit}" if len(d) > limit else ""))
    return out


def uses_of(repo, f, sym):
    """-> (files that use `sym` besides `f`, word_search_only). Exact refs when the language server knows the symbol, else a word search (flagged)."""
    raw = [r for r in lp(repo, "nav", "refs", f, sym).splitlines() if r.strip()]
    text = any("word-match" in r for r in raw)
    used = [hit_file(r) for r in raw if not r.startswith("#") and r != "(none)" and hit_file(r) and not r.startswith(f + ":")]
    return list(dict.fromkeys(used)), text
