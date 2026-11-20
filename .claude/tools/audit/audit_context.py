#!/usr/bin/env python3
"""audit_context: exact context for the auditors: what the stage changed and who uses it, plus (local model, optional) unchanged files it can break.

    python3 -I /root/.claude/tools/audit/audit_context.py <repo> --base master --head dev --out .work/S/audits/audit-context.md [--verdict] [--workspace W] [--no-model]

--workspace W: refresh the code index of W/pipeline.json `context.index` first and use its `context` settings. --no-model: leave out "Related, unchanged".

For each changed code file: its `lp nav outline`. For each symbol defined on an added line: `lp nav refs` (callers, other files first).
Never fails the stage: a missing language server only leaves a line out."""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from pipeline.configuration import METRICS_LOG
from pipeline.util import swallowed
from search.leadparts import definitions, is_test, uses_of
from search.lpcall import added_ranges, lp

MAX_FILES, MAX_SYMS, MAX_DEFS, MAX_REFS, BUDGET_S, MAX_CHARS = (
    40,
    30,
    12,
    4,
    280,
    6000,
)  # 6000 for the mechanical part; the model section adds at most ~1700 (see related_files)
RELATED_DOWN = "(related files: skipped, local model down)"  # packed: the auditors read it first, so it stays small


def _file_line(f, added, new):
    """`path +ranges  new: name@line ...` for one changed file; tests only get a `(test)` mark."""
    ranges = ",".join(f"{x}-{y}" if x != y else str(x) for x, y in added[:6]) or "none"
    if is_test(f):
        return f"{f} +{ranges}  (test)"
    return f"{f} +{ranges}" + (f"  new: {' '.join(f'{n}@{ln}' for ln, n in new[:MAX_DEFS])}" if new else "")


def _sym_line(repo, f, sym):
    used, text = uses_of(repo, f, sym)
    used = sorted(used)  # lp nav refs order is not stable between runs
    if not used:
        return f"  {'~' if text else ''}{sym}: 0 files (no other use: possibly unused)"
    tail = ": " + ", ".join(used[:MAX_REFS]) + (f" +{len(used) - MAX_REFS}" if len(used) > MAX_REFS else "")
    return f"  {'~' if text else ''}{sym}: {len(used)} files" + tail


def _file_section(repo, f, added, nsym, syms=None):
    """-> (lines, nsym) for one file, or (None, nsym) when `lp nav` has no outline for it. SYMS, a dict, receives {file: [new names]}."""
    outline = lp(repo, "nav", "outline", f).splitlines()
    if not outline:
        return None, nsym
    new = definitions(outline, lambda ln: any(x <= ln <= y for x, y in added))  # definitions that start on an added line
    lines = [_file_line(f, added, new)]
    if syms is not None:
        syms[f] = [n for _ln, n in new]
    for _ln, sym in new:
        if nsym >= MAX_SYMS or is_test(f):
            break
        nsym += 1
        lines.append(_sym_line(repo, f, sym))
    return lines, nsym


def _footer(code, noout, cut):
    lines = []
    if noout:
        lines.append(
            "Changed files with no outline from `lp nav` (docs, data, assets, or a language it has no server for; read the diff): "
            + ", ".join(noout[:30])
            + (f" (+{len(noout) - 30})" if len(noout) > 30 else "")
        )
    if cut:
        lines.append(f"(stopped after {BUDGET_S}s: remaining files not listed)")
    if len(code) > MAX_FILES:
        lines.append(f"(+{len(code) - MAX_FILES} more changed code files not listed)")
    return lines


def _refresh_index(workspace):
    """Same refresh as `plan.py run` (search.ctx_config.ensure_index) so the lookups see the merged code; never raises, Strata is not needed."""
    if not workspace:
        return
    try:
        from search.ctx_config import ensure_index

        ensure_index(workspace)
    except Exception as ex:  # best effort: a missing module, bad pipeline.json or failing lp only costs the refresh
        swallowed("audit_context.ensure_index", ex)


def _log_event(out, **fields):
    """The `context-audit` stat line in the stage's metrics.jsonl (next to the audit-context.md file); never raises."""
    try:
        from pipeline.util import append_jsonl

        d = os.path.dirname(os.path.abspath(out))
        append_jsonl(os.path.join(d, METRICS_LOG), {"ev": "context-audit", "ts": time.time(), "stage": os.path.basename(d), **fields})
    except Exception as ex:  # fail open on purpose
        swallowed("audit_context.metrics", ex)


def _related(repo, changed, syms, workspace):
    """-> (lines, status): the "Related, unchanged" section from the local model, lazily imported; status is ok|down|none."""
    try:
        from localmodel import related_files
        from search.ctx_config import ctx_config

        cfg = ctx_config(workspace) if workspace else None
        found = related_files.related_unchanged(repo, changed, syms, cfg)
    except Exception as ex:  # best effort: no Strata, no module, a bad answer
        swallowed("audit_context.related", ex)
        found = None
    if found is None:
        return [RELATED_DOWN], "down"
    return ["", "## Related, unchanged (files outside the diff a change can break; local model, each checked by a symbol or module-name hit)"] + (
        found or ["(none verified)"]
    ), "ok" if found else "none"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--workspace", default=None, help="folder of pipeline.json: refresh its `context.index` repos before the lookups (best effort)")
    ap.add_argument("--no-model", action="store_true", help="skip the local-model 'Related, unchanged' section")
    a = ap.parse_args(argv)
    _refresh_index(a.workspace)
    ch = added_ranges(a.repo, a.base, a.head)
    code, noout = sorted(ch), []
    lines = [
        f"# Audit context ({a.base}..{a.head}): {len(ch)} changed files",
        "Exact `lp nav` answers, no model. new = definitions on added lines (name@line); `sym`: refs in other files; `~` = word search, not verified as the same symbol.",
    ]
    syms: dict = {}
    nsym, t0, cut = 0, time.time(), False
    for f in code[:MAX_FILES]:
        if time.time() - t0 > BUDGET_S or sum(map(len, lines)) > MAX_CHARS:
            cut = True
            break
        section, nsym = _file_section(a.repo, f, ch[f], nsym, syms)
        if section is None:
            noout.append(f)  # no section for a file `lp nav` knows nothing about
            continue
        lines += section
    lines += _footer(code, noout, cut)
    if not a.no_model:
        rel, status = _related(a.repo, sorted(ch), syms, a.workspace)
        lines += rel
        _log_event(a.out, chars=sum(map(len, lines)), files=len(ch), related=max(len(rel) - 2, 0) if status == "ok" else 0, related_status=status)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(("VERDICT: DONE\n" if a.verdict else "") + f"audit-context: {len(code) - len(noout)} of {len(ch)} files with outline, {nsym} symbols -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
