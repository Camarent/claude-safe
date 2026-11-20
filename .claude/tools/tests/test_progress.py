"""Synthetic test of the one-line status."""

import json
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import progress
from pipeline.ctx import Ctx
from tests.support.tmpdirs import tracked_mkdtemp


class StatusLine(unittest.TestCase):
    def test_wave_task_counts_agents_and_phase_position(self):
        d = tracked_mkdtemp()
        feat = os.path.join(d, ".work", "F")
        stage = os.path.join(feat, "phases", "02-b")
        os.makedirs(stage)
        with open(os.path.join(feat, "Plan.md"), "w") as f:
            f.write(
                "## Phases\n| dir | goal | done | deps | paths | status |\n|---|---|---|---|---|---|\n"
                + "".join(f"| {n} | g | true | | src/{n} | todo |\n" for n in ("01-a", "02-b", "03-c"))
            )
        with open(os.path.join(stage, "TASKS.md"), "w") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n"
                + "".join(f"| task-{i} | x | dev | 1 | wave {1 if i < 3 else 2}, haiku |\n" for i in range(1, 6))
                + "\n## Graph\nwave 1: task-1, task-2 | wave 2: task-3, task-4, task-5\n"
            )
        ev = [
            {"ev": "dispatch", "id": "a1", "task": "task-1", "role": "dev"},
            {"ev": "report", "id": "a1", "task": "task-1", "role": "reviewer", "verdict": "PASS"},
            {"ev": "dispatch", "id": "a2", "task": "task-3", "role": "dev"},
            {"ev": "dispatch", "id": "a3", "task": "task-4", "role": "dev"},
            {"ev": "dispatch", "id": "a4", "task": "final-test", "role": "script"},
        ]
        with open(os.path.join(stage, "orch.jsonl"), "w") as f:
            f.write("".join(json.dumps(e) + "\n" for e in ev))
        c = Ctx(SimpleNamespace(stage="F/phases/02-b", dir=d))
        c.disp = 7
        c.cost = 0.4
        line = progress.status_line(c, [{"id": "a2", "agent": "dev"}, {"id": "a3", "agent": "dev"}])
        self.assertEqual(line, "[p2/3 02-b] wave 2/2 · tasks 1/5 done · dev×2 · 7 Sonnet/Opus calls · $0.40")
        self.assertIn("final-test", progress.status_line(c, [{"id": "a4", "agent": "script"}]))  # a stage-end step shows its id
        plain = os.path.join(d, ".work", "plain")
        os.makedirs(plain)
        open(os.path.join(plain, "TASKS.md"), "w").close()
        self.assertTrue(progress.status_line(Ctx(SimpleNamespace(stage="plain", dir=d)), []).startswith("[plain]"))  # an ordinary stage: just its name


if __name__ == "__main__":
    unittest.main()
