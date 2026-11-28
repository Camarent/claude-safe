"""Plan check: a doc that cites a path a task changes must be in that task's Predicted paths.

The plan-time twin of docs_impact.check (which runs on the job diff): the Predicted paths stand for the diff. A doc under docs_dir that cites a non-doc Predicted path of the task is a finding unless the doc is itself a Predicted path of the task, or a doc-only task of the stage lists it. The stage's docs gate (pipeline.json, `Docs gate: off` in TASKS.md) and the exempt globs apply as at run time."""

import os
import re

from . import docs_impact
from .configuration import (
    DOCS_IMPACT_DEFAULT,
    DOCS_IMPACT_EXEMPT_KEY,
    DOCS_IMPACT_KEY,
    DOCS_IMPACT_PATH_RE,
    DOCS_IMPACT_PLAN_PREFIX,
    TASKS_FILE,
    docs_dir_of,
    project,
)
from .plan_files import paths_in, read_entries
from .plan_rules import _is_docs_path
from .util import read_text


def check_docs_impact(d, tid, workspace) -> list[str]:
    """-> [`docs-impact:` problem] for each doc under docs_dir of `workspace` that cites a non-doc Predicted path of task `tid` while the doc is in neither `tid`'s Predicted paths nor a doc-only task's of the stage `d`. [] when the stage's docs gate is off (pipeline.json, `Docs gate: off` in TASKS.md) or the project has no docs_dir."""
    cfg = project(workspace)
    docs = docs_dir_of(cfg)
    if not docs or not docs_impact.gate_on(cfg.get(DOCS_IMPACT_KEY, DOCS_IMPACT_DEFAULT), os.path.basename(d)):
        return []
    if docs_impact.plan_off(read_text(os.path.join(d, TASKS_FILE))):
        return []
    own = paths_in(read_text(os.path.join(d, tid + ".md")))
    code = sorted(p for p in own if not _is_docs_path(p))
    if not code:
        return []
    msgs = _citing_docs_msgs(workspace, docs, tid, own, _doc_only_listed(d, tid), cfg.get(DOCS_IMPACT_EXEMPT_KEY, []), code)
    return sorted(msgs)


def _doc_only_listed(d, tid) -> set[str]:
    """-> docs that a doc-only task of the stage `d` other than `tid` lists in its Predicted paths."""
    listed: set[str] = set()
    for e in read_entries(d):
        other = paths_in(read_text(os.path.join(d, e["id"] + ".md"))) if e["id"] != tid else []
        if other and all(_is_docs_path(p) for p in other):
            listed.update(other)
    return listed


def _citing_docs_msgs(workspace, docs, tid, own, listed, exempt, code) -> set[str]:
    """-> `docs-impact:` problems for each doc under `docs` that cites a path of `code` and is in neither `own` nor `listed`."""
    msgs: set[str] = set()
    for doc in docs_impact.doc_files(workspace, docs):
        if doc in own or doc in listed or docs_impact._exempt(doc, exempt):
            continue
        text = docs_impact._doc_text(workspace, doc)
        if text is None:
            continue
        cited = docs_impact._cited_paths(text)
        msgs.update(
            f"{DOCS_IMPACT_PLAN_PREFIX} {tid}: {doc} cites {p} which the task changes, but {doc} is not in its Predicted paths"
            for p in code
            if p in cited or re.search(DOCS_IMPACT_PATH_RE.format(path=re.escape(p)), text)
        )
    return msgs
