"""republish brief: a `publish` entry of pipeline.json adds its build recipe per URL; a URL without one gets the no-recipe rule."""

import argparse
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.configuration import PUBLISH_KEY, SCRATCH_DIR, WORK_DIR, ConfigError, publish_entries
from pipeline.phases import _publish_steps

URL = "https://claude.ai/artifact/course-a-page"
OTHER = "https://claude.ai/artifact/other"
PUB_LINE = f" {URL}\n"
ENTRY = {
    "cwd": "site",
    "build": "make build OUT={out}",
    "page": "dist/index.html",
    "files": {"app.js": "dist/app.js"},
}


class RepublishRecipe(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="synthetic-republish-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.a = argparse.Namespace(dir=self.d, stage="s1")

    def steps(self, entries, pub_line=PUB_LINE):
        c = types.SimpleNamespace(
            a=self.a,
            flags={"index": False, "ui": False, "live": False},
            S="s1",
            cfg={PUBLISH_KEY: publish_entries({PUBLISH_KEY: entries})},
            pub=True,
            pub_line=pub_line,
        )
        return [s for batch in _publish_steps(c) for s in batch]

    def prompt(self, entries, pub_line=PUB_LINE):
        (step,) = self.steps(entries, pub_line)
        self.assertEqual(step["id"], "republish")
        return step["prompt"]

    def test_entry_adds_build_cwd_and_page(self):
        text = self.prompt({URL: ENTRY})
        out_dir = os.path.join(os.path.abspath(self.d), WORK_DIR, "s1", SCRATCH_DIR)
        self.assertIn("make build OUT=", text)
        self.assertIn(os.path.join(os.path.abspath(self.d), "site"), text)
        self.assertIn("`dist/index.html`", text)
        self.assertIn("`app.js` from `dist/app.js`", text)
        self.assertIn(out_dir, text)
        self.assertNotIn("{out}", text)
        self.assertIn("never create a new artifact", text)
        self.assertIn("omit the icon", text)
        self.assertIn("VERDICT: DONE|FAIL", text)

    def test_out_placeholder_in_page_and_files_is_the_scratch_dir(self):
        entry = {"build": "make", "page": "{out}/course-a/index.html", "files": {"app.js": "{out}/course-a/app.js"}}
        text = self.prompt({URL: entry})
        out_dir = os.path.join(os.path.abspath(self.d), WORK_DIR, "s1", SCRATCH_DIR)
        self.assertIn(f"`{out_dir}/course-a/index.html`", text)
        self.assertIn(f"`app.js` from `{out_dir}/course-a/app.js`", text)
        self.assertNotIn("{out}", text)

    def test_url_without_entry_gets_no_recipe_rule(self):
        text = self.prompt({URL: ENTRY}, pub_line=f" {OTHER}\n")
        self.assertNotIn("make build", text)
        self.assertNotIn(URL, text)
        self.assertIn("no build recipe configured, report FAIL if the page cannot be built", text)

    def test_entry_is_only_added_for_urls_on_the_publish_line(self):
        text = self.prompt({URL: ENTRY, OTHER: {**ENTRY, "build": "echo other"}}, pub_line=PUB_LINE)
        self.assertIn("make build", text)
        self.assertNotIn("echo other", text)

    def test_base_text_kept_and_no_files_clause_when_none(self):
        text = self.prompt({URL: {"build": "make", "page": "index.html"}})
        self.assertIn("read the `PUBLISH:` line in .work/s1/TASKS.md", text)
        self.assertNotIn("plus these files", text)
        self.assertIn("`index.html`", text)

    def test_default_cwd_is_project_dir(self):
        text = self.prompt({URL: {"build": "make", "page": "index.html"}})
        self.assertIn(f"run `make` in `{os.path.normpath(os.path.abspath(self.d))}`", text)

    def test_chained_build_is_refused(self):
        with self.assertRaises(ConfigError):
            publish_entries({PUBLISH_KEY: {URL: {"build": "make && make install", "page": "x"}}})

    def test_bad_shape_is_refused(self):
        with self.assertRaises(ConfigError):
            publish_entries({PUBLISH_KEY: {URL: {"page": "x"}}})
        with self.assertRaises(ConfigError):
            publish_entries({PUBLISH_KEY: ["not", "a", "map"]})

    def test_absent_setting_is_empty(self):
        self.assertEqual(publish_entries({}), {})

    def test_every_publish_line_counts(self):
        text = self.prompt({URL: ENTRY, OTHER: {**ENTRY, "build": "echo other"}}, pub_line=f" {URL} {OTHER}")
        self.assertIn("make build", text)
        self.assertIn("echo other", text)

    def test_url_prefix_does_not_match(self):
        text = self.prompt({URL: ENTRY}, pub_line=f" {URL}-v2\n")
        self.assertNotIn("make build", text)

    def test_cwd_and_file_keys_cannot_leave_the_project(self):
        for bad in ({"cwd": "../x"}, {"cwd": "/etc"}, {"cwd": "a/../../x"}, {"files": {"../x.js": "f"}}, {"files": {"/abs.js": "f"}}):
            with self.assertRaises(ConfigError, msg=str(bad)):
                publish_entries({PUBLISH_KEY: {URL: {"build": "make", "page": "x", **bad}}})
