"""pipeline.configuration resolves names lazily: unpatched imports work, mock.patch restores, unknown names raise AttributeError."""

import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ConfigurationLazyTest(unittest.TestCase):
    def test_private_name_imports_unpatched(self):
        from pipeline.configuration import _PROJECT_CACHE

        project_mod = importlib.import_module("pipeline.configuration.project")
        self.assertIs(_PROJECT_CACHE, project_mod._PROJECT_CACHE)

    def test_function_imports_unpatched(self):
        from pipeline.configuration import docs_dir_of

        cite_check = importlib.import_module("pipeline.configuration.cite_check")
        self.assertIs(docs_dir_of, cite_check.docs_dir_of)

    def test_resolved_name_is_cached_in_module_globals(self):
        import pipeline.configuration as configuration

        self.addCleanup(configuration.__dict__.pop, "docs_dir_of", None)
        configuration.__dict__.pop("docs_dir_of", None)
        first = configuration.docs_dir_of
        self.assertIs(configuration.__dict__["docs_dir_of"], first)

    def test_patch_restores_original(self):
        import pipeline.configuration as configuration

        original = configuration._PROJECT_CACHE
        with mock.patch("pipeline.configuration._PROJECT_CACHE", {"patched": True}):
            self.assertEqual(configuration._PROJECT_CACHE, {"patched": True})
        self.assertIs(configuration._PROJECT_CACHE, original)

    def test_patch_restores_function(self):
        import pipeline.configuration as configuration
        from pipeline.configuration import cite_check

        with mock.patch("pipeline.configuration.docs_dir_of", return_value="x"):
            self.assertEqual(configuration.docs_dir_of({}), "x")
        self.assertIs(configuration.docs_dir_of, cite_check.docs_dir_of)

    def test_unknown_name_raises_attribute_error(self):
        import pipeline.configuration as configuration

        missing = "NO_SUCH_CONSTANT_FOR_LAZY_TEST"
        with self.assertRaises(AttributeError):
            getattr(configuration, missing)

    def test_dir_lists_submodule_names(self):
        import pipeline.configuration as configuration

        self.assertIn("docs_dir_of", dir(configuration))
        self.assertIn("_PROJECT_CACHE", dir(configuration))


if __name__ == "__main__":
    unittest.main()
