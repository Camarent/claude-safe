"""Synthetic tests: the Sonnet/Opus dispatch limit formula and the tools-check relevance filter."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gate_mode, limits
from pipeline.configuration import (
    GATE_LEVEL_FULL,
    GATE_LEVEL_MERGE,
    GATE_LEVEL_UNIT,
    GATE_MODE_FLAG_RE,
    GATE_MODE_FULL,
    GATE_STATS_P90,
    HEADER_LINES_MAX,
)
from pipeline.extra_tools import split_needed
from tests.support.tmpdirs import tracked_mkdtemp


def tasks_md(n_haiku, n_sonnet, flags=""):
    d = tracked_mkdtemp()
    rows = "".join(f"| task-{i} | x | haiku | 1 | wave 1, haiku |\n" for i in range(1, n_haiku + 1)) + "".join(
        f"| task-{n_haiku + i} | x | sonnet | 1 | wave 1, sonnet |\n" for i in range(1, n_sonnet + 1)
    )
    p = os.path.join(d, "TASKS.md")
    with open(p, "w") as f:
        f.write("flags: " + flags + "\n| task | what | owner | price | review |\n|---|---|---|---|---|\n" + rows)
    return p


class HeavyLimit(unittest.TestCase):
    def test_fixed_part_follows_the_flags_and_tasks_add_their_multiplier(self):
        plain = limits.heavy_limit(tasks_md(0, 0))
        self.assertEqual(plain[1], 6)  # 2 audits + 1 audit-fix architect + 2 re-audits + 1 test-fix architect (no UI)
        ui = limits.heavy_limit(tasks_md(0, 0, "ui"))
        self.assertEqual(ui[1] - plain[1], 2)  # ux audit + web-test fix architect
        self.assertEqual(limits.heavy_limit(tasks_md(0, 0, "no-audit"))[1], 1)  # without audits only the test-fix architect is left
        lim, fixed, per = limits.heavy_limit(tasks_md(10, 5))
        self.assertEqual((lim, per), (fixed + 15 * limits.PER_HAIKU_TASK, 15 * limits.PER_HAIKU_TASK))  # sonnet words in the plan are ignored

    def test_config_overrides_the_multipliers(self):
        a = limits.heavy_limit(tasks_md(4, 0, "no-audit no-test"), {"heavy_per_haiku_task": 5})
        self.assertEqual(a, (20, 0, 20))


class ToolsNeeded(unittest.TestCase):
    def test_only_tools_named_by_the_audit_file_can_fail_the_check(self):
        bad = ["clangd 1:19.0-63", "cargo-audit 0.22", "cppcheck 2.17"]
        real, warn = split_needed(bad, "Tools: cargo-audit, clippy")
        self.assertEqual((real, warn), (["cargo-audit 0.22"], ["clangd 1:19.0-63", "cppcheck 2.17"]))
        self.assertEqual(split_needed(bad, "")[0], [])  # no AUDIT.md: nothing is required


if __name__ == "__main__":
    unittest.main()


class ConsistencyAt30Tasks(unittest.TestCase):
    """A healthy 30-task stage (all Haiku, UI) must not come near any limit; a runaway one must."""

    def setUp(self):
        self.md = tasks_md(30, 0, "ui")
        self.evs = lambda heavy, light: (
            [{"ev": "dispatch", "agent": "dev", "model": "sonnet"}] * heavy
            + [{"ev": "dispatch", "agent": "dev", "model": "haiku"}] * light
            + [{"ev": "dispatch", "agent": "script", "model": None}] * 30
        )

    def test_haiku_and_script_dispatches_never_use_up_the_heavy_budget(self):
        from pipeline.limits import heavy_count, session_guard, stage_limit

        os.environ.pop("ORCH_MAX_DISPATCH", None)
        self.assertEqual(heavy_count(self.evs(0, 100)), 0)  # 100 Haiku + 30 script dispatches: no heavy use
        self.assertEqual(stage_limit(self.md), 68)  # 8 fixed (UI) + 30 x 2
        self.assertGreater(stage_limit(self.md), 35)  # the middle-case estimate (~15-35 heavy calls) is far under it
        self.assertGreaterEqual(
            session_guard(self.md), 30 * 9
        )  # a task needs at most 9 model sessions: the loop guard clears a stage where every task hits the worst path

    def test_env_override_wins(self):
        from pipeline.limits import stage_limit

        os.environ["ORCH_MAX_DISPATCH"] = "12"
        try:
            self.assertEqual(stage_limit(self.md), 12)
        finally:
            os.environ.pop("ORCH_MAX_DISPATCH", None)


class GateMode(unittest.TestCase):
    def test_mode_of_command_is_its_first_mode_flag_or_full(self):
        self.assertEqual(gate_mode.mode_of("scripts/dev.sh gate --unit"), "--unit")
        self.assertEqual(gate_mode.mode_of("scripts/dev.sh gate --merge --py"), "--merge")
        self.assertEqual(gate_mode.mode_of("scripts/dev.sh test"), GATE_MODE_FULL)
        self.assertEqual(gate_mode.mode_of(""), GATE_MODE_FULL)

    def test_header_modes_are_the_flags_of_the_header_comments(self):
        text = "#!/bin/sh\n# usage: gate --unit | --merge | --py\nrun all\n"
        self.assertEqual(gate_mode.header_modes(text), {"--unit", "--merge", "--py"})
        self.assertEqual(gate_mode.header_modes('"""Gate script: --unit or --merge."""\nprint(1)\n'), {"--unit", "--merge"})
        self.assertEqual(gate_mode.header_modes("# no mode named here\nprint(1)\n"), set())

    def test_header_flag_below_the_limit_is_ignored(self):
        text = "\n" * HEADER_LINES_MAX + "# --unit\n"
        self.assertEqual(gate_mode.header_modes(text), set())

    def test_mode_flag_regex_is_the_default_word_flag(self):
        self.assertIsNotNone(re.search(GATE_MODE_FLAG_RE, "x --unit y"))
        self.assertIsNone(re.search(GATE_MODE_FLAG_RE, "x a--unit y"))

    def test_gate_mode_module_names_no_project(self):
        with open(gate_mode.__file__, encoding="utf-8") as f:
            self.assertNotIn("kata", f.read())

    def test_gate_stats_groups_and_p90_fraction_are_named_limits(self):
        self.assertEqual((GATE_LEVEL_UNIT, GATE_LEVEL_MERGE, GATE_LEVEL_FULL), ("unit", "merge", "full"))
        self.assertEqual(GATE_STATS_P90, 0.9)
