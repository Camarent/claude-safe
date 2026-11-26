"""First audit pass: sec, ux and arch in one batch; final-docs beside them only when it edits no file the stage diff touches (real tmp repo)."""

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases, graph


class DocsOverlapTest(unittest.TestCase):
    def test_pure(self):
        self.assertTrue(audit_phases.docs_overlap(("docs/",), ["docs/a.md"]))
        self.assertTrue(audit_phases.docs_overlap(("CLAUDE.md",), ["CLAUDE.md"]))
        self.assertFalse(audit_phases.docs_overlap(("docs/", "CLAUDE.md"), ["src/docs.py", "x/CLAUDE.md"]))


class BesideTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        self.g = ["git", "-C", self.d, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*self.g, "init", "-q", "-b", "master"], check=True)
        subprocess.run([*self.g, "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        subprocess.run([*self.g, "checkout", "-q", "-b", "dev"], check=True)
        self.a = argparse.Namespace(dir=self.d, stage="s")
        self.cfg = {"branch_release": "master", "branch_work": "dev"}

    def commit(self, rel):
        os.makedirs(os.path.dirname(os.path.join(self.d, rel)) or self.d, exist_ok=True)
        with open(os.path.join(self.d, rel), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        subprocess.run([*self.g, "add", "-A"], check=True)
        subprocess.run([*self.g, "commit", "-q", "-m", rel], check=True)

    def beside(self):
        return audit_phases.docs_beside_audits(self.a, self.cfg, self.d)

    def test_disjoint_diff_runs_beside(self):
        self.commit("src/x.py")
        self.assertTrue(self.beside())

    def test_decoy_overlap_stays_serial(self):
        self.commit("src/x.py")
        self.commit("docs/guide.md")  # decoy: a file the docs writer edits is in the diff
        self.assertFalse(self.beside())

    def test_empty_diff_stays_serial(self):
        self.assertFalse(self.beside())


class FirstPassTest(unittest.TestCase):
    def batches(self, first, beside=()):
        a = argparse.Namespace(dir="/x", stage="s")
        with mock.patch.object(audit_phases, "_audit_step", side_effect=lambda a, c, f, h, s, k, *r: {"id": k}):
            return [[x["id"] for x in b] for b in audit_phases._audit_passes(a, {}, {}, False, False, [], first, beside)]

    def test_all_audits_in_one_batch(self):
        self.assertEqual(self.batches([("audit-sec", "sec-auditor"), ("audit-ux", "ux-reviewer")]), [["audit-sec", "audit-ux", "audit-arch"]])

    def test_docs_joins_the_first_batch(self):
        self.assertEqual(self.batches([("audit-sec", "sec-auditor")], [{"id": "final-docs"}]), [["final-docs", "audit-sec", "audit-arch"]])


class RetroDigestTest(unittest.TestCase):
    def test_failed_digest_does_not_block(self):
        a = argparse.Namespace(dir="/x", stage="s")
        with tempfile.TemporaryDirectory() as t:
            a.dir = t
            evs: list = []
            seen: dict = {}

            def fake(a, evs, steps, n):
                seen["ok"] = steps[0]["ok"]
                return None

            with mock.patch.object(graph, "run_phase", side_effect=fake), mock.patch.object(graph, "is_finish_phase", return_value=False):
                graph._retro_phases(a, evs, 0)
            self.assertIn("FAIL", seen["ok"])
