"""The merge gate logs one line per poll (attempt, state, elapsed), and only pr_ops._sweep asks gh for statusCheckRollup.

Injected clock and fake gh: no real sleep, no network. The field scan reads the pipeline sources with ast.
"""

import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_ops
from pipeline.configuration import CI_POLL_S, GH_CHECKS_PENDING_RC

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
FIELD = "statusCheckRollup"
ALLOWED_FUNC = "_sweep"


def R(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeClock:
    """Time moves only when the code sleeps; every sleep is recorded."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Scripted:
    """Runner: the first key that prefixes the space-joined command wins; a list is consumed in order (its last answer repeats)."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, args, cwd=None, timeout=120):
        line = " ".join(args)
        self.calls.append(line)
        for k, v in self.table.items():
            if line.startswith(k):
                q = v if isinstance(v, list) else [v]
                return q.pop(0) if len(q) > 1 else q[0]
        return R(0)


def view(field, value):
    return R(0, json.dumps({field: value}))


OPEN_PR = {
    "gh pr view b --json state": view("state", "OPEN"),
    "gh pr view b --json isDraft": view("isDraft", False),
}


class PollLogsEachRead(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def test_each_read_logs_attempt_state_and_elapsed(self):
        reads = iter([None, None, "ok"])
        with mock.patch.object(pr_ops, "_say") as say:
            got = pr_ops._poll(lambda: next(reads), 100, self.clock, what="checks of b")
        self.assertEqual(got, "ok")
        self.assertEqual(
            [c.args[0] for c in say.call_args_list],
            [
                "checks of b: attempt 1, state pending, elapsed 0s",
                f"checks of b: attempt 2, state pending, elapsed {CI_POLL_S}s",
                f"checks of b: attempt 3, state ready, elapsed {2 * CI_POLL_S}s",
            ],
        )

    def test_timeout_logs_the_last_pending_read(self):
        with mock.patch.object(pr_ops, "_say") as say:
            got = pr_ops._poll(lambda: None, 2 * CI_POLL_S - 1, self.clock, what="checks of b")
        self.assertIsNone(got)
        lines = [c.args[0] for c in say.call_args_list]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[-1], f"checks of b: attempt 3, state pending, elapsed {2 * CI_POLL_S}s")


class MergeGateLogsEachPoll(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.clock = FakeClock()

    def test_merge_logs_one_line_per_poll_of_mergeability_and_checks(self):
        table = {
            **OPEN_PR,
            "gh pr view b --json mergeable": [view("mergeable", "UNKNOWN"), view("mergeable", "MERGEABLE")],
            "gh pr checks b": [R(GH_CHECKS_PENDING_RC, "pending"), R(GH_CHECKS_PENDING_RC, "pending"), R(0, "all green")],
            "gh pr merge b": R(0),
        }
        run = Scripted(table)
        with mock.patch.object(pr_ops, "_run", run), mock.patch.object(pr_ops, "_say") as say:
            note = pr_ops._merge(self.d, self.d, {}, "s", "b", self.clock)
        self.assertEqual(note, "PR squash-merged: chore(s): s")
        self.assertEqual(
            [c.args[0] for c in say.call_args_list],
            [
                "mergeability of b: attempt 1, state pending, elapsed 0s",
                f"mergeability of b: attempt 2, state ready, elapsed {CI_POLL_S}s",
                "checks of b: attempt 1, state pending, elapsed 0s",
                f"checks of b: attempt 2, state pending, elapsed {CI_POLL_S}s",
                f"checks of b: attempt 3, state ready, elapsed {2 * CI_POLL_S}s",
            ],
        )


def _field_lists_outside(source, func):
    """-> line numbers of string constants that are comma-separated field lists naming statusCheckRollup, outside every `def func`."""
    tree = ast.parse(source)
    spans = [(f.lineno, f.end_lineno or f.lineno) for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == func]
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if "," in node.value and FIELD in node.value.split(",") and not any(a <= node.lineno <= b for a, b in spans):
            hits.append(node.lineno)
    return sorted(hits)


class StatusRollupOnlyInSweep(unittest.TestCase):
    def test_no_gh_field_list_names_statusCheckRollup_outside_sweep(self):
        found = {}
        for path in sorted(PIPELINE_DIR.glob("*.py")):
            lines = _field_lists_outside(path.read_text(encoding="utf-8"), ALLOWED_FUNC)
            if lines:
                found[path.name] = lines
        self.assertEqual(found, {}, f"{FIELD} outside {ALLOWED_FUNC}: add it to {ALLOWED_FUNC} only")

    def test_sweep_itself_still_asks_for_the_field(self):
        src = (PIPELINE_DIR / "pr_ops.py").read_text(encoding="utf-8")
        self.assertTrue(_field_lists_outside(src, "nothing_named_this"), "the scan must see the field in _sweep")

    def test_mutation_second_call_with_the_field_is_caught(self):
        src = (PIPELINE_DIR / "pr_ops.py").read_text(encoding="utf-8")
        mutated = src + '\n\ndef _mutant(repo):\n    return _run(["gh", "pr", "list", "--json", "number,statusCheckRollup"], repo)\n'
        self.assertEqual(len(_field_lists_outside(mutated, ALLOWED_FUNC)), 1)


if __name__ == "__main__":
    unittest.main()
