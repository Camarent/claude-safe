"""Characterization tests for plan_check.publish_recipe_problems: every PUBLISH: url needs a `publish` recipe."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check

URL = "https://x/a"


class PublishRecipeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)

    def write_config(self, cfg):
        with open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def write_tasks(self, text):
        with open(os.path.join(self.d, "TASKS.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def recipe(self, url=URL):
        return {"publish": {url: {"cwd": "course", "build": "make", "page": "i.html"}}}

    def test_publish_url_with_recipe_passes(self):
        self.write_config(self.recipe())
        self.write_tasks(f"# Stage\n\nPUBLISH: {URL}\n")
        self.assertEqual(plan_check.publish_recipe_problems(self.d), [])

    def test_publish_url_without_recipe_fails(self):
        self.write_config(self.recipe("https://x/other"))
        self.write_tasks(f"# Stage\n\nPUBLISH: {URL}\n")
        got = plan_check.publish_recipe_problems(self.d)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(plan_check.PUBLISH_RECIPE_PREFIX))
        self.assertIn(URL, got[0])
        self.assertEqual(plan_check.errors_of(got), got)  # a problem: it fails the check

    def test_bad_publish_config_yields_none(self):
        self.write_config({"publish": ["not", "a", "map"]})
        self.write_tasks(f"# Stage\n\nPUBLISH: {URL}\n")
        self.assertEqual(plan_check.publish_recipe_problems(self.d), [])

    def test_stage_without_publish_line_needs_nothing(self):
        self.write_config(self.recipe("https://x/other"))
        self.write_tasks("# Stage\n")
        self.assertEqual(plan_check.publish_recipe_problems(self.d), [])
