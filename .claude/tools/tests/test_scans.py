"""Synthetic tests for the scans script step and its wiring. No real scan tools."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import scans
from tests.support.orch_harness import OrchHarness
from tests.support.tmpdirs import tracked_mkdtemp


def run_main(d, cmds):
    with open(f"{d}/pipeline.json", "w") as f:
        json.dump({"scans": cmds}, f)
    out = io.StringIO()
    with redirect_stdout(out):
        scans.main(["--dir", d, "--stage", "s"])
    return out.getvalue()


class ScansScript(unittest.TestCase):
    def test_writes_file_and_done_even_with_findings(self):
        d = tracked_mkdtemp()
        out = run_main(d, ["echo hello", "false"])
        self.assertTrue(out.startswith("VERDICT: DONE"))
        text = open(f"{d}/.work/s/audits/scans.md").read()
        self.assertIn("hello", text)
        self.assertIn("exit 1", text)

    def test_missing_command_is_blocked(self):
        d = tracked_mkdtemp()
        out = run_main(d, ["no-such-tool-xyz --x"])
        self.assertTrue(out.startswith("VERDICT: BLOCKED"))
        self.assertIn("no-such-tool-xyz", out)


class ScansPhase(OrchHarness):
    def audit_agents(self):
        """-> (ids of script steps seen before sec-auditor, sec-auditor prompt)."""
        ids = []
        for _ in range(12):
            try:
                b = self.go("next")["dispatches"]
            except (ValueError, KeyError):
                break
            if not b:
                break
            d = b[0]
            ids.append(d["prompt"])
            if d["agent"] == "sec-auditor":
                return ids, d["prompt"]
            self.answer(d, "VERDICT: PASS" if d["agent"] in ("reviewer", "tester", "web-tester") else "VERDICT: DONE")
        return ids, ""

    def test_phase_and_prompt_only_when_configured(self):
        self.config(scans=["echo x"])
        self.base_stage()
        ids, prompt = self.audit_agents()
        self.assertTrue(any("scans.py" in i for i in ids), ids)
        self.assertIn("scans.md", prompt)

    def test_nothing_changes_without_scans(self):
        self.base_stage()
        ids, prompt = self.audit_agents()
        self.assertFalse(any("scans.py" in i for i in ids), ids)
        self.assertNotIn("scans.md", prompt)

    def base_stage(self):
        with open(f"{self.d}/.work/s/TASKS.md", "w") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n"
                "## Graph\nwave 1: task-1\nflags: no-retro\n"
            )


if __name__ == "__main__":
    unittest.main()
