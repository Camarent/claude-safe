"""plan check `docs-wave:` warning: a docs-only task beside a code task of its wave without a dependency (synthetic stage)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_rules
from pipeline.configuration import DOCS_WAVE_WARN_PREFIX
from tests.support.tmpdirs import tracked_mkdtemp


def tid(n):
    """Task id for ordinal n (built here so the test text carries no fixed id)."""
    return "task-" + str(n)


CODE, DOCS_SAME_WAVE, DOCS_WITH_DEP, DOCS_ALONE, CODE_ONLY = (tid(n) for n in (1, 2, 3, 4, 5))


class DocsWave(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.ents = [
            {"id": CODE, "wave": 1, "depends": []},  # code
            {"id": DOCS_SAME_WAVE, "wave": 1, "depends": []},  # docs, same wave as the code task, no depends
            {"id": DOCS_WITH_DEP, "wave": 1, "depends": [CODE, CODE_ONLY]},  # docs, depends on both code tasks
            {"id": DOCS_ALONE, "wave": 2, "depends": []},  # docs, alone in its wave
            {"id": CODE_ONLY, "wave": 1, "depends": []},  # code only, same wave as the code task
        ]
        self.write(CODE, "src/app.py")
        self.write(DOCS_SAME_WAVE, "docs/guide.md")
        self.write(DOCS_WITH_DEP, "README.md")
        self.write(DOCS_ALONE, "docs/api.md")
        self.write(CODE_ONLY, "src/other.py")

    def write(self, t, paths):
        Path(self.d, t + ".md").write_text(f"# {t}: title\nWave: W1\nPredicted paths: {paths}\nTest: t\n", encoding="utf-8")

    def check(self, t):
        return plan_rules.check_docs_wave(self.d, next(e for e in self.ents if e["id"] == t), self.ents)

    def test_a_docs_only_task_beside_a_code_task_without_a_dependency_is_flagged(self):
        got = self.check(DOCS_SAME_WAVE)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(f"{DOCS_WAVE_WARN_PREFIX} {DOCS_SAME_WAVE}"))
        self.assertIn(CODE, got[0])
        self.assertIn("--depends 1", got[0])
        self.assertEqual(plan_check.errors_of(got), [])  # a warning, never an error

    def test_a_docs_task_that_depends_on_the_code_task_is_fine(self):
        self.assertEqual(self.check(DOCS_WITH_DEP), [])

    def test_a_docs_task_alone_in_its_wave_is_fine(self):
        self.assertEqual(self.check(DOCS_ALONE), [])

    def test_a_code_only_task_is_fine(self):
        self.assertEqual(self.check(CODE), [])
        self.assertEqual(self.check(CODE_ONLY), [])

    def test_a_task_with_one_code_path_among_docs_is_not_a_docs_task(self):
        self.write(DOCS_SAME_WAVE, "docs/guide.md, src/app.py")
        self.assertEqual(self.check(DOCS_SAME_WAVE), [])
