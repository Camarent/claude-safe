#!/usr/bin/env python3
"""replay_gate: run past escalation-gate cases through the SAME input builder and judge as the real gate; never writes gate.jsonl, always exits 0.

    python3 -I replay_gate.py CASES.json      (see replay_cases.example.json)

CASES.json is a list of {name, task_file, failure_files: [oldest ... newest], notes_file, rules_file, expected: PASS|FAIL}. Needs the local model (Strata) up.
Prints one line per case (name, expected, got, kind, reason, seconds) and a summary."""

import json
import os
import sys
import time

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]  # -I drops the script dir
from pipeline.configuration import FAIL, PASS  # noqa: E402

from localmodel import escalation_gate  # noqa: E402


def replay_case(case, llm=None):
    """-> (got PASS|FAIL|NONE, kind, reason, seconds). Same builder and verdict rule as the real gate (escalation_gate.gate_input / verdict_of)."""
    files = [f for f in case.get("failure_files") or [] if f]
    prompt, newest = escalation_gate.gate_input(case["task_file"], files, case.get("notes_file", ""), case.get("rules_file", ""))
    t0 = time.time()
    if escalation_gate.security_escalation(newest):
        return PASS, "", "security-risk ESCALATE: not judged", round(time.time() - t0, 1)
    j = escalation_gate.judge(prompt, llm)
    v, reason, kind, _shown, veto = escalation_gate.verdict_of(j, newest)
    got = FAIL if veto else (PASS if v else "NONE")  # FAIL without a quote from the newest report is not a veto: the real gate goes on
    return got, kind, reason, round(time.time() - t0, 1)


def main(argv=None, llm=None, out=sys.stdout):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__, file=out)
        return 0
    with open(argv[0], encoding="utf-8") as f:
        cases = json.load(f)
    right = 0
    for i, c in enumerate(cases, 1):
        got, kind, reason, secs = replay_case(c, llm)
        ok = got == c.get("expected")
        right += bool(ok)
        print(
            f"{'ok ' if ok else 'BAD'} {c.get('name', i)}: expected {c.get('expected')} got {got} kind={kind or '-'} {secs}s | {reason}", file=out, flush=True
        )
    print(f"summary: {right}/{len(cases)} as expected", file=out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
