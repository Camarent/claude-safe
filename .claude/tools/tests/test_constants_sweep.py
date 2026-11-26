"""Guard: literals that moved to pipeline.configuration must not reappear in the swept modules."""

import os
import re
import sys
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)

VERDICT_RE = re.compile(r'"(PASS|FAIL|DONE|BLOCKED|ESCALATE|CLEAN)"')
VERDICT_FILES = (
    "pipeline/steps.py",
    "testrun.py",
    "pipeline/pr_ops.py",
    "localmodel/prereview.py",
    "pipeline/cli_wave.py",
    "pipeline/triggers.py",
    "pipeline/graph.py",
    "merge_wave.py",
    "ops_steps.py",
    "localmodel/escalation_gate.py",
    "localmodel/localdev.py",
)
EVENT_RE = re.compile(r'\["ev"\] *[!=]= *"(report|dispatch|skip|session|retry)"')
EVENT_FILES = tuple(
    "pipeline/" + n
    for n in ("audit_phases.py", "cli_orch.py", "cli_wave.py", "graph.py", "limits.py", "progress.py", "requests.py", "steps.py", "triggers.py", "driver.py")
)
FILE_RE = re.compile(r'"(\.work|TASKS\.md|orch\.jsonl|metrics\.jsonl|BLOCKED\.md|Plan\.md)"')
FILE_FILES = (
    "pipeline/branches.py",
    "pipeline/worktree.py",
    "pipeline/pr_ops.py",
    "pipeline/steps.py",
    "pipeline/cli_wave.py",
    "pipeline/graph.py",
    "pipeline/ctx.py",
    "pipeline/requests.py",
    "pipeline/triggers.py",
    "pipeline/progress.py",
    "pipeline/phases.py",
    "pipeline/plan_insert.py",
    "retro/lead_cost.py",
    "retro/incidents.py",
    "testrun.py",
    "ux_run.py",
    "pipeline/util.py",
    "ops_steps.py",
    "audit/scans.py",
    "localmodel/localdev.py",
    "localmodel/escalation_gate.py",
    "pipeline/plan_files.py",
    "pipeline/plan_check.py",
)
TIMEOUT_RE = re.compile(r"timeout=(300|900|120|60|3600|1500|30)\b")
TIMEOUT_FILES = (
    "pipeline/pr_ops.py",
    "pipeline/extra_tools.py",
    "pipeline/graph.py",
    "ux_run.py",
    "pipeline/cli_feature.py",
    "pipeline/briefs.py",
    "testrun.py",
    "audit/audit_run.py",
    "localmodel/localdev.py",
    "search/localq.py",
    "pipeline/configuration/gitbranch.py",
)
MODEL_RE = re.compile(r'"(haiku|sonnet|opus)"')
MODEL_FILES = tuple(
    "pipeline/" + n for n in ("requests.py", "limits.py", "cli_plan.py", "phases.py", "triggers.py", "steps.py", "plan_run.py", "graph.py", "audit_phases.py")
)
THRESHOLD_RE = re.compile(r"10\*\*6|steps < 500|limit=14000|n > 99|> 0\.25|> 0\.2:|max_turns=25|max_tokens=2500|goal\[:(6000|3000)\]")
THRESHOLD_FILES = (
    "pipeline/triggers.py",
    "pipeline/cli_wave.py",
    "pipeline/worktree.py",
    "pipeline/worktrees.py",
    "pipeline/plan_files.py",
    "localmodel/localdev.py",
)
MISC_RE = re.compile(r'f?"VERDICT: |"/opt/ask-venv')
MISC_FILES = ("pipeline/driver.py", "ux_run.py", "index_step.py", "ops_steps.py", "search/local.py")
# (name, pattern, files): one row per swept family
FAMILIES = [
    ("verdict word", VERDICT_RE, VERDICT_FILES),
    ("event name", EVENT_RE, EVENT_FILES),
    ("file name", FILE_RE, FILE_FILES),
    ("timeout", TIMEOUT_RE, TIMEOUT_FILES),
    ("model name", MODEL_RE, MODEL_FILES),
    ("threshold", THRESHOLD_RE, THRESHOLD_FILES),
    ("misc", MISC_RE, MISC_FILES),
]


class SweepGuardTest(unittest.TestCase):
    def test_no_literal_reappears(self):
        hits = []
        for name, pat, files in FAMILIES:
            for rel in files:
                with open(os.path.join(TOOLS, rel), encoding="utf-8") as f:
                    for n, line in enumerate(f, 1):
                        if pat.search(line):
                            hits.append(f"{rel}:{n} {name}: {line.strip()[:80]}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
