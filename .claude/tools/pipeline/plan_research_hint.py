"""The research-hint rule (a warning, never an error): a task whose Goal or Done names a fact the repo may lack (`unknown`, `check whether`, `verify that X supports`, `investigate`, `find out`) should depend on a `Type: research` task that finds the fact first. A research task itself is exempt: it is the fix."""

import os
import re

from .configuration import RESEARCH_HINT_PREFIX
from .plan_files import task_type
from .task_text import lines
from .util import read_text

TRIGGER_RE = re.compile(r"unknown|check whether|verify that .* supports|investigate|find out", re.I)


def _text(d, tid) -> str:
    return read_text(os.path.join(d, tid + ".md"))


def check_research_hint(d, ents) -> list[str]:
    """`research-hint:` warnings for the entries of ENTS (the stage's entries) whose Goal/Done text has a trigger word and that depend on no research task of the stage."""
    research = {e["id"] for e in ents if task_type(_text(d, e["id"])) == "research"}
    probs = []
    for e in ents:
        text = _text(d, e["id"])
        if e["id"] in research or research & set(e["depends"]):
            continue
        goal = " ".join(value for _, value in lines(text, "Goal", "Done"))
        if TRIGGER_RE.search(goal):
            probs.append(
                f"{RESEARCH_HINT_PREFIX} {e['id']}: its Goal or Done needs a fact the repo may lack: add a `Type: research` task that finds it and depend on it"
            )
    return probs
