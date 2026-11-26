#!/usr/bin/env python3
"""escalation_gate: a one-way veto before the dev ladder pays for Sonnet. Fails open.

    python3 -I /root/.claude/tools/localmodel/escalation_gate.py --stage S --task task-N --dir D --failure REPORT_FILE [--failure REPORT_FILE ...] [--notes plan-notes.md] [--rules AGENTS.md]

Shows the local model (thinking on) the brief, plan notes, project rules and the failure reports (only the newest is the live failure) and asks whether the dev can finish the task with the brief and repo as they are. `VERDICT: FAIL` = no (a fact no file supplies, an owner-only choice no rule settles, or a blocker outside editing; kind plan_error = the brief contradicts itself): the driver sends the task to the lead. `VERDICT: DONE` = go on with the ladder, also when the model is unreachable or gives no usable answer. Every call is appended to .work/<stage>/gate.jsonl."""

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]  # -I drops the script dir
from pipeline.configuration import (  # noqa: E402
    DONE,
    FAIL,
    GATE_ANSWER_TOKENS,
    GATE_EARLIER_CHARS,
    GATE_KINDS,
    GATE_NEWEST_CHARS,
    GATE_NOTES_CHARS,
    GATE_PLAN_ERROR,
    GATE_RULES_CHARS,
    GATE_TASK_CHARS,
    PASS,
    WORK_DIR,
)
from pipeline.util import swallowed  # noqa: E402

from localmodel.prompts import VERDICT_ANSWER  # noqa: E402


def read(path, n):
    try:
        return open(path, errors="replace").read()[:n]
    except FileNotFoundError:
        return ""  # a missing notes or rules file is left out (gate_input)
    except OSError as ex:
        swallowed("escalation_gate.read", ex)
        return ""


CRITERIA = (
    "The text has a task brief, the stage's plan notes, the project rules (AGENTS.md), earlier failed attempts (context only) and the NEWEST failure report: the live failure. Predicted paths are only a guess: the dev may edit any file the change needs.\n"
    "Question: can the dev finish this task with the brief and the repo exactly as they are? Judge the NEWEST report only. An earlier attempt may repeat the error but never decides.\n"
    "PASS when the newest report's question or blocker is settled by the brief, the plan notes, AGENTS.md or by a file in the repo that the dev can look up; "
    "when the dev's report already gives the answer, or lists options and one follows from those rules; "
    "when it is a defect in the work (wrong or missing content, red test, other files to update), a stale or old report, the task's own uncommitted edits (the next attempt continues from them; a snapshot update is a file edit), "
    "or a weaker model that gave up or ran out of context or turns.\n"
    "FAIL only when the newest report names a fact that no file in the brief, plan notes, AGENTS.md or repo supplies, "
    "or an owner-only choice that no rule settles, or a blocker outside editing (denied permission, missing external input or tool, broken environment, a wrong Test path). "
    'The words "lead must decide", "decision needed" or "owner" alone never decide: check whether a rule settles it.\n'
    f'On FAIL add "kind": one of {", ".join(GATE_KINDS)}. Use {GATE_PLAN_ERROR} when the brief contradicts a test, an invariant or another rule in the brief, and start the reason with "plan error:". '
    "The evidence must be one exact line of the NEWEST report."
)


# The reviewer's security risk classes (agents/reviewer.md): a matching ESCALATE line goes to Sonnet without the judge.
SECURITY_RISK = re.compile(
    r"\b(?:path|subprocess|shell|eval|exec|concatenat\w*|HTML|SQL|regex|parser|deletes?|deleting|overwrit\w*|renam\w*|schema|migration|id|auth|secrets?|network)\b",  # whole words: "filepath", "executed", "evaluate" do not count
    re.I,
)


def security_escalation(failure):
    """True when a failure report carries an ESCALATE line naming one of the security risk classes."""
    m = re.search(r"^\s*(?:VERDICT:\s*)?ESCALATE:\s*(.*)$", failure, re.M)
    return bool(m and SECURITY_RISK.search(m[1]))


def section(title, text):
    return f"{title}\n{text}\n\n" if text.strip() else ""


def build_prompt(task, newest, earlier="", notes="", rules=""):
    """The judge prompt: criteria, then brief, plan notes, rules, earlier attempts (context) and the newest report (the live failure)."""
    return (
        f"Criteria:\n{CRITERIA}\n\n"
        + section("TASK BRIEF", task)
        + section("PLAN NOTES (plan-notes.md)", notes)
        + section("PROJECT RULES (AGENTS.md)", rules)
        + section("EARLIER ATTEMPTS (context only, never the deciding line)", earlier)
        + f"NEWEST FAILURE REPORT (the live failure)\n{newest}\n\nDoes the text meet ALL the criteria? Be strict. "
        + VERDICT_ANSWER
    )


def judge(prompt, llm=None):
    """-> dict with verdict/reason/evidence; {} when the model is unreachable or answers nothing parsable."""
    try:
        parse: Callable[[str], dict]
        if llm is None:
            from search.strata import strata_up

            from localmodel import askmodel

            if not strata_up():
                return {}
            llm = lambda p: askmodel.llm(p, GATE_ANSWER_TOKENS, think=True)  # noqa: E731
            parse = askmodel.jsonobj
        else:
            parse = json.loads
        return parse(llm(prompt)) or {}
    except Exception as ex:  # fail open: the ladder goes on as it did before the gate
        swallowed("escalation_gate.judge", ex)
        return {}


def read_failures(files):
    """-> (earlier, newest): the reports are oldest first; the last is the live failure (whole budget), the others are short context, each headed with its file name."""
    files = [f for f in files if f]
    if not files:
        return "", ""
    earlier = "\n".join(f"--- earlier attempt {i + 1} ({os.path.basename(f)}) ---\n{read(f, GATE_EARLIER_CHARS)}" for i, f in enumerate(files[:-1]))
    return earlier, f"--- {os.path.basename(files[-1])} ---\n{read(files[-1], GATE_NEWEST_CHARS)}"


def gate_input(task_file, failure_files, notes_file="", rules_file=""):
    """-> (prompt, newest report text). The one input builder of the real gate and of replay_gate.py; a missing notes or rules file is left out."""
    files = [failure_files] if isinstance(failure_files, str) else list(failure_files)
    task = read(task_file, GATE_TASK_CHARS)
    earlier, newest = read_failures(files)
    notes, rules = read(notes_file, GATE_NOTES_CHARS), read(rules_file, GATE_RULES_CHARS)
    return build_prompt(task, newest, earlier, notes, rules), newest


def verdict_of(j, newest):
    """-> (verdict word, reason, kind, shown quote, veto). A veto needs a quote that is a line of the NEWEST report; a kind outside GATE_KINDS is dropped."""
    v = str(j.get("verdict", "")).upper()
    ev = str(j.get("evidence", "")).strip()
    shown = ev if ev and " ".join(ev.split()) in " ".join(newest.split()) else ""
    kind = str(j.get("kind", "")).strip().lower()
    return v, str(j.get("reason", "")).strip(), kind if kind in GATE_KINDS else "", shown, v == FAIL and bool(shown)


def run(stage, task_id, d, failure_files, llm=None, notes_file="", rules_file=""):
    """-> (verdict word, text). FAIL = veto; DONE = go on (PASS, or no usable answer)."""
    prompt, newest = gate_input(os.path.join(d, WORK_DIR, stage, f"{task_id}.md"), failure_files, notes_file, rules_file)
    t0 = time.time()
    if security_escalation(newest):  # never vetoed: the judge is not asked
        j = {"verdict": PASS, "reason": "security-risk ESCALATE: routed to Sonnet, not judged", "evidence": ""}
    else:
        j = judge(prompt, llm)
    v, reason, kind, shown, veto = verdict_of(j, newest)
    out = FAIL if veto else DONE
    if not v:
        text = "gate skipped: local model unreachable or no usable answer"
    else:
        text = (
            f"judge {v}"
            + (f" [{kind}]" if kind else "")
            + f": {reason}"
            + (f" | {shown}" if shown else "")
            + ("" if veto or v != FAIL else " (no quoted line: not a veto)")
        )
    try:
        with open(os.path.join(d, WORK_DIR, stage, "gate.jsonl"), "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": int(t0),
                        "task": task_id,
                        "judge": v or "NONE",
                        "veto": veto,
                        "kind": kind,
                        "reason": reason,
                        "evidence": shown,
                        "seconds": round(time.time() - t0, 1),
                        "think": True,
                        "failures": [failure_files] if isinstance(failure_files, str) else list(failure_files),
                    }
                )
                + "\n"
            )
    except OSError as ex:
        swallowed("escalation_gate.run gate.jsonl", ex)  # the log is best effort; the verdict still goes out
    return out, text


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--dir", default=".")
    ap.add_argument("--failure", action="append", default=[], help="failure report, oldest first; the last is the live failure")
    ap.add_argument("--notes", default="", help="the stage's plan-notes.md (optional)")
    ap.add_argument("--rules", default="", help="the project repo's AGENTS.md (optional)")
    a = ap.parse_args(argv)
    v, text = run(a.stage, a.task, a.dir, a.failure, notes_file=a.notes, rules_file=a.rules)
    print(f"VERDICT: {v}\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
