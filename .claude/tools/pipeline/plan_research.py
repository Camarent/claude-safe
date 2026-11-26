"""The research-task rule: a `Type: research` task writes one tracked output (its `Outputs:` path), and a later task must consume it (depend on the research task and name the path). Code and report tasks are not checked here."""

import os

from .configuration import RESEARCH_PREFIX
from .plan_files import task_outputs, task_type
from .plan_outputs import check_output_paths
from .util import read_text


def _consumed(d, tid, out, ents) -> bool:
    """True when a later entry of ENTS depends on TID and its task text names the output path OUT."""
    ids = [x["id"] for x in ents]
    later = ents[ids.index(tid) + 1 :] if tid in ids else []
    return any(tid in x["depends"] and out in read_text(os.path.join(d, x["id"] + ".md")) for x in later)


def check_research(d, e, ents, repo) -> list[str]:
    """`research:` errors of research task E (ENTS: the stage's entries): one `Outputs:` path, which is tracked (plan_outputs rule), and a later task that depends on E and names it. A non-research task -> []."""
    tid = e["id"]
    text = read_text(os.path.join(d, tid + ".md"))
    if task_type(text) != "research":
        return []
    outs = task_outputs(text)
    if len(outs) != 1:
        return [f"{RESEARCH_PREFIX} {tid}: a research task names one tracked output in its Outputs: line, found {len(outs)}"]
    out = outs[0]
    problems = check_output_paths(d, tid, repo)
    if not _consumed(d, tid, out, ents):
        problems.append(f"{RESEARCH_PREFIX} {tid}: no later task depends on {tid} and names its output `{out}` in its text")
    return problems
