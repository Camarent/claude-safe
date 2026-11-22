"""final-docs brief: the index module list, the fold rule and the docs index check."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import phases
from tests.support.gitrepo import git as git_init


def _make_repo(root, modules, git=True, docs=True):
    if git:
        git_init(root, "init", "-q")
    if docs:
        os.makedirs(os.path.join(root, "docs"))
    for mod in modules:
        os.makedirs(os.path.join(root, mod))
        with open(os.path.join(root, mod, "a.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")


class DocsStepModules(unittest.TestCase):
    def _step_text(self, root):
        a = SimpleNamespace(stage="s1", dir=root)
        return phases._docs_step(a, {})["prompt"]

    def test_brief_lists_index_modules_and_fold_rule(self):
        with tempfile.TemporaryDirectory() as root:
            _make_repo(root, ["alpha", "beta"])
            text = self._step_text(root)
        self.assertIn("Index modules: alpha (`alpha`), beta (`beta`).", text)
        self.assertIn("Write docs/modules/<slug>.md only for a listed module", text)
        self.assertIn("fold the content of a folder that is not an index module into its parent module doc", text)

    def test_unlisted_folder_is_not_an_index_module(self):
        with tempfile.TemporaryDirectory() as root:
            _make_repo(root, ["alpha", "beta", "gamma"])
            with open(os.path.join(root, "docs", "modules.txt"), "w", encoding="utf-8") as f:
                f.write("alpha\nbeta\n")
            text = self._step_text(root)
        self.assertIn("Index modules: alpha (`alpha`), beta (`beta`).", text)
        self.assertNotIn("gamma (`gamma`)", text)

    def test_brief_tells_writer_to_run_docs_check(self):
        with tempfile.TemporaryDirectory() as root:
            _make_repo(root, ["alpha"])
            text = self._step_text(root)
        self.assertIn("Before VERDICT: DONE run `cs-docs-index --root <repo root> --check`", text)
        self.assertIn("`describes no existing module`", text)

    def test_no_module_list_without_git_repo(self):
        with tempfile.TemporaryDirectory() as root:
            _make_repo(root, ["alpha", "beta"], git=False)
            text = self._step_text(root)
        self.assertNotIn("Index modules:", text)
        self.assertNotIn("fold the content", text)
        self.assertIn("Before VERDICT: DONE run `cs-docs-index", text)

    def test_no_module_list_without_docs_folder(self):
        with tempfile.TemporaryDirectory() as root:
            _make_repo(root, ["alpha", "beta"], docs=False)
            text = self._step_text(root)
        self.assertNotIn("Index modules:", text)
        self.assertIn("Before VERDICT: DONE run `cs-docs-index", text)

    def test_repo_setting_resolves_subfolder(self):
        with tempfile.TemporaryDirectory() as ws:
            os.makedirs(os.path.join(ws, "sub"))
            _make_repo(os.path.join(ws, "sub"), ["alpha", "beta"])
            a = SimpleNamespace(stage="s1", dir=ws)
            text = phases._docs_step(a, {"repo": "sub"})["prompt"]
        self.assertIn("Index modules: alpha (`alpha`), beta (`beta`).", text)
