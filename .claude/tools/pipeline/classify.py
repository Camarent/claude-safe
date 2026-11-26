"""Failure classifier: one answer to "can a model fix this?", shared by the driver steps and the retro.

Classes: CODE (a model can fix it: the bounded ladder), PLAN (the brief or Test line is wrong: lead or architect, no dev retry), INFRA (tool or environment: lead, stop), UNKNOWN (lead).
"""

import os
import re

from .configuration import ENV_ERROR_PATTERN
from .plan_check import check_test_globs

CODE, PLAN, INFRA, UNKNOWN = "code", "plan", "infra", "unknown"
STOP = (PLAN, INFRA, UNKNOWN)  # classes that go to the lead on the first occurrence, never up a model ladder

# qrun's own infra lines only, anchored at line start: a test name or an app error must not look like infra.
INFRA_MARKERS = re.compile(r"^(qrun: cannot run|FAIL exit 126\b|FAIL exit 127\b)", re.M)
ENV_ERROR = re.compile(ENV_ERROR_PATTERN)


def env_failure(text) -> bool:
    """True when the output names a missing environment piece (missing module, no config path, command not found): a repair can fix it, a dev cannot."""
    return ENV_ERROR.search(text) is not None


def classify_test_run(output, stage_dir, tid, repo) -> tuple[str, str]:
    """-> (class, reason) for a failed task test run (qrun output text).

    qrun exit 3 prints `SKIPPED/EMPTY: zero tests detected` or `SKIPPED/EMPTY: N skipped`. Zero tests with a Test path that matches no file and that no Predicted path creates is a plan defect; with the file present or promised by Predicted paths it is code (the dev wrote no test or an empty one). Skips are code.
    """
    low = output.lower()
    if INFRA_MARKERS.search(output):
        return INFRA, "test command cannot run (tool, path or permission)"
    if "skipped/empty: zero tests detected" in low:
        probs = check_test_globs(stage_dir, tid, repo) if os.path.isfile(os.path.join(stage_dir, tid + ".md")) else []
        if probs:
            return PLAN, probs[0]
        return CODE, "zero tests detected although the Test path exists or is created by the task"
    if "skipped/empty:" in low:
        return CODE, "tests skipped (a skip hides failures)"
    if "fail" in low or "not ok" in low or "error" in low:
        return CODE, "tests failed"
    return UNKNOWN, "failure without a recognised signal"
