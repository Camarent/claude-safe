"""The closing `NN-finish` phase of a feature and the feature-retro problems file."""

import argparse
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch
from pipeline import plan_files as pf
from retro import feature_retro
from tests.support.tmpdirs import tracked_mkdtemp


class FinishPhaseTest(unittest.TestCase):
    def test_retro_step_names_feature_on_last_phase(self):
        with tempfile.TemporaryDirectory() as d:
            feat = os.path.join(d, ".work", "F")
            for n in ("01-a", "02-b"):
                os.makedirs(os.path.join(feat, "phases", n))
            rows = "| 01-a | g | true | | | done | 1 |\n| 02-b | g | true | 01-a | | todo | 1 |\n"
            open(os.path.join(feat, "Plan.md"), "w").write("## Phases\n" + pf.PHASE_HEAD + rows)
            self.assertFalse(pf.is_last_phase(os.path.join(feat, "phases", "01-a")))
            self.assertTrue(pf.is_last_phase(os.path.join(feat, "phases", "02-b")))
            self.assertFalse(pf.is_last_phase(feat))
            last = cli_orch._retro_step(argparse.Namespace(dir=d, stage="F/phases/02-b"))
            self.assertIn("retro/feature_retro.py --feature-dir .work/F", last)
            self.assertIn(".work/F/retro.md", last)
            mid = cli_orch._retro_step(argparse.Namespace(dir=d, stage="F/phases/01-a"))
            self.assertIn("orch.py retro --stage F/phases/01-a", mid)
            self.assertNotIn("feature_retro", mid)

    def test_names(self):
        self.assertTrue(pf.is_finish_dir("03-finish"))
        self.assertFalse(pf.is_finish_dir("03-finish-line"))
        self.assertFalse(pf.is_finish_dir("finish"))

    def test_finish_tasks_md_and_earlier_phases(self):
        with tempfile.TemporaryDirectory() as d:
            feat = os.path.join(d, ".work", "F")
            for n in ("01-a", "02-finish"):
                os.makedirs(os.path.join(feat, "phases", n))
            open(os.path.join(feat, "phases", "01-a", "TASKS.md"), "w").write("x")
            open(os.path.join(feat, "phases", "02-finish", "TASKS.md"), "w").write("x")
            rows = "| 01-a | g | true | | | done | 12 |\n| 02-finish | g | true | 01-a | | todo | 0 |\n"
            open(os.path.join(feat, "Plan.md"), "w").write("## Phases\n" + pf.PHASE_HEAD + rows)
            fin = os.path.join(feat, "phases", "02-finish", "TASKS.md")
            self.assertTrue(pf.is_finish_phase(fin))
            self.assertFalse(pf.is_finish_phase(os.path.join(feat, "phases", "01-a", "TASKS.md")))
            self.assertEqual(pf.earlier_phase_tasks_mds(fin), [os.path.join(feat, "phases", "01-a", "TASKS.md")])
            self.assertFalse(pf.is_mid_phase(fin))  # the finish phase is the last row: master merge, push and cleanup run there
            self.assertTrue(pf.is_mid_phase(os.path.join(feat, "phases", "01-a", "TASKS.md")))

    def test_ensure_finish_adds_once(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".work", "F"))
            a = type("A", (), {"dir": d, "feature": "F"})()
            open(os.path.join(d, ".work", "F", "Plan.md"), "w").write("## Phases\n" + pf.PHASE_HEAD + "| 01-a | g | true | | | done | 12 |\n")
            self.assertTrue(pf.ensure_finish(a))
            self.assertFalse(pf.ensure_finish(a))
            self.assertEqual([p["dir"] for p in pf.read_phases(a)], ["01-a", "02-finish"])

    def _ensure_finish_on(self, phase_dir):
        d = tracked_mkdtemp()
        os.makedirs(os.path.join(d, ".work", "F"))
        a = type("A", (), {"dir": d, "feature": "F"})()
        open(os.path.join(d, ".work", "F", "Plan.md"), "w").write("## Phases\n" + pf.PHASE_HEAD + f"| {phase_dir} | g | true | | | done | 12 |\n")
        return pf.ensure_finish(a)

    def test_ensure_finish_after_phase_99_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "99"):
            self._ensure_finish_on("99-a")  # 100-finish would break the NN- folder names

    def test_feature_retro_lists_repeats(self):
        with tempfile.TemporaryDirectory() as d:
            for ph, sig in (("01-a", "wave-cost: x"), ("02-b", "wave-cost: y\nRETRO context-recall: z")):
                os.makedirs(os.path.join(d, "phases", ph))
                os.makedirs(os.path.join(d, "phases", ph, "retro"))
                open(os.path.join(d, "phases", ph, "retro", "retro-numbers.md"), "w").write("".join(f"RETRO {s}\n" for s in sig.split("\nRETRO ")))
            text = feature_retro.render("F", feature_retro.gather(d))
            self.assertIn("- wave-cost: 01-a, 02-b", text)
            self.assertNotIn("no retro.md", text)


if __name__ == "__main__":
    unittest.main()
