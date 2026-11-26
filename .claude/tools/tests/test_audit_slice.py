"""Block slice of plan-vs-changes for the auditors (audit_context.block_rows / _block_section) and the 1 KB digest for fix architects (phase_context.brief_digest)."""

import os
import sys
import tempfile
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_context as C
from pipeline import audit_phases
from pipeline import phase_context as PC
from pipeline.audit_phases import reaudit_context_steps

PVC = "\n".join(
    [
        "# Plan vs changes (main..dev), by script",
        "",
        "## task-1: Block one",
        "Predicted paths: a/b.py",
        "Changed under these paths: a/b.py",
        "## task-2: Decoy",
        "Predicted paths: xa/b.py",
        "Changed under these paths: xa/b.py, other/c.py",
        "## Changed but not in any task's Predicted paths",
        "- other/c.py",
    ]
)


class BlockRows(unittest.TestCase):
    def test_keeps_title_and_only_rows_of_block_paths(self):
        out = C.block_rows(PVC, ["a/b.py"])
        self.assertEqual(out[0], "# Plan vs changes (main..dev), by script")
        self.assertIn("Changed under these paths: a/b.py", out)
        self.assertIn("Predicted paths: a/b.py", out)

    def test_decoy_path_sharing_a_suffix_is_not_inlined(self):
        out = C.block_rows(PVC, ["a/b.py"])
        self.assertFalse(any("xa/b.py" in x for x in out))  # C3: a path not in the block never appears inlined

    def test_path_not_in_block_is_not_inlined(self):
        out = C.block_rows(PVC, ["a/b.py"])
        self.assertFalse(any("other/c.py" in x for x in out))

    def test_no_block_paths_keeps_only_title(self):
        self.assertEqual(C.block_rows(PVC, []), [PVC.splitlines()[0]])

    def test_empty_text(self):
        self.assertEqual(C.block_rows("", ["a/b.py"]), [])


class BlockSection(unittest.TestCase):
    def test_missing_file_gives_no_section(self):
        self.assertEqual(C._block_section(None, ["a/b.py"]), [])
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(C._block_section(os.path.join(d, "missing.md"), ["a/b.py"]), [])

    def test_names_full_path_and_inlines_block_rows_only(self):
        with tempfile.TemporaryDirectory() as d:
            pvc = os.path.join(d, "plan-vs-changes.md")
            with open(pvc, "w", encoding="utf-8") as fh:
                fh.write(PVC + "\n")
            out = C._block_section(pvc, ["a/b.py"])
            self.assertIn(pvc, out[1])
            self.assertIn("Changed under these paths: a/b.py", out)
            self.assertFalse(any("xa/b.py" in x or "other/c.py" in x for x in out))  # C3 decoy


class BriefDigest(unittest.TestCase):
    def test_capped_at_1024_bytes(self):
        req = "r" * 5000
        ctx = "c" * 5000
        out = PC.brief_digest(req, ctx)
        self.assertLessEqual(len(out.encode("utf-8")), 1024)
        self.assertTrue(out.startswith("## request (head)"))

    def test_multibyte_cut_stays_valid_utf8(self):
        out = PC.brief_digest("é" * 2000, "")
        self.assertLessEqual(len(out.encode("utf-8")), 1024)  # cut mid-character drops the partial byte, no error

    def test_short_inputs_kept_whole(self):
        out = PC.brief_digest("the request", "the leads")
        self.assertIn("the request", out)
        self.assertIn("the leads", out)


class BlockArgs(unittest.TestCase):
    def test_audit_context_step_gets_the_block_paths(self):
        a = SimpleNamespace(stage="S", dir="/p", feature=None)
        cfg = {"branch_release": "master", "repo": ""}
        with (
            unittest.mock.patch.object(audit_phases, "audit_base", return_value="B"),
            unittest.mock.patch.object(audit_phases.gitx, "try_text", return_value="a/b.py\nc d.py\n"),
        ):
            sts = reaudit_context_steps(a, cfg, "/p", True, "-2")
        cmd = sts[0]["prompt"]
        self.assertIn("--plan-vs-changes .work/S/audits/plan-vs-changes.md", cmd)
        self.assertIn("--block 'a/b.py,c d.py'", cmd)
        self.assertNotIn("xa/b.py", cmd)

    def test_no_paths_no_args(self):
        a = SimpleNamespace(stage="S", dir="/p", feature=None)
        with unittest.mock.patch.object(audit_phases.gitx, "try_text", return_value=""):
            self.assertEqual(audit_phases.block_args(a, {"branch_release": "m"}, "/p"), "")
