"""configuration settings of the driver-limits phase: limit_recheck_s and script_timeout_s (tool settings), and the PROJECT_DEFAULTS keys
acceptance_timing_flag, pipeline_install_check and ci_available."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import docs_impact
from pipeline.configuration import DOCS_IMPACT_KEY, LIMIT_RECHECK_S, PROJECT_DEFAULTS, SCRIPT_TIMEOUT_S, literals
from pipeline.configuration.env import ENV_OVERRIDES, LOCAL_CONFIG_FILE
from pipeline.configuration.errors import ConfigError
from pipeline.configuration.project import project

SWALLOWED = "pipeline.util.swallowed"


class ToolSettingP4Test(unittest.TestCase):
    def _resolve(self, name, env=None, block=None):
        """-> tool_setting(name) run from a fresh workspace holding pipeline.json `tool_settings` = block (None = no file)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        if block is not None:
            with open(os.path.join(tmp.name, "pipeline.json"), "w", encoding="utf-8") as f:
                json.dump({literals.TOOL_SETTINGS_KEY: block}, f)
        var = literals.TOOL_ENV_PREFIX + name
        with mock.patch.dict(os.environ):
            os.environ.pop(var, None)
            if env is not None:
                os.environ[var] = env
            with mock.patch("os.getcwd", return_value=tmp.name):
                return literals.tool_setting(name)

    def _key(self, name):
        return literals._SETTINGS[name][1]

    def test_limit_recheck_default_is_the_constant(self):
        self.assertEqual(LIMIT_RECHECK_S, 900)
        self.assertEqual(self._resolve("LIMIT_RECHECK_S"), 900)

    def test_limit_recheck_env_and_pipeline_json_override(self):
        self.assertEqual(self._resolve("LIMIT_RECHECK_S", env="60"), 60)
        self.assertEqual(self._resolve("LIMIT_RECHECK_S", block={self._key("LIMIT_RECHECK_S"): 120}), 120)

    def test_limit_recheck_bad_value_falls_back(self):
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("LIMIT_RECHECK_S", env="0"), 900)
        sw.assert_called_once()

    def test_script_timeout_default_is_the_constant(self):
        self.assertEqual(SCRIPT_TIMEOUT_S, 1800.0)
        self.assertEqual(self._resolve("SCRIPT_TIMEOUT_S"), 1800.0)

    def test_script_timeout_pipeline_json_raises_it(self):
        self.assertEqual(self._resolve("SCRIPT_TIMEOUT_S", block={self._key("SCRIPT_TIMEOUT_S"): 3600}), 3600.0)
        self.assertEqual(self._resolve("SCRIPT_TIMEOUT_S", env="2400"), 2400.0)

    def test_script_timeout_bad_values_fall_back(self):
        for bad in (0, -5, "nan", True, "soon"):
            with self.subTest(bad=bad), mock.patch(SWALLOWED) as sw:
                self.assertEqual(self._resolve("SCRIPT_TIMEOUT_S", block={self._key("SCRIPT_TIMEOUT_S"): bad}), 1800.0)
            sw.assert_called_once()


class ProjectDefaultsP4Test(unittest.TestCase):
    def _project(self, block=None):
        """-> project() of a fresh workspace holding pipeline.json = block (None = no file)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        if block is not None:
            with open(os.path.join(tmp.name, "pipeline.json"), "w", encoding="utf-8") as f:
                json.dump(block, f)
        return project(tmp.name)

    def test_defaults(self):
        self.assertIsNone(PROJECT_DEFAULTS["acceptance_timing_flag"])
        self.assertIsNone(PROJECT_DEFAULTS["pipeline_install_check"])
        self.assertIs(PROJECT_DEFAULTS["ci_available"], True)
        cfg = self._project()
        self.assertIsNone(cfg["acceptance_timing_flag"])
        self.assertIsNone(cfg["pipeline_install_check"])
        self.assertIs(cfg["ci_available"], True)

    def test_project_accepts_the_keys(self):
        cfg = self._project({"acceptance_timing_flag": "--durations=10", "pipeline_install_check": "python3 -m pip check", "ci_available": False})
        self.assertEqual(cfg["acceptance_timing_flag"], "--durations=10")
        self.assertEqual(cfg["pipeline_install_check"], "python3 -m pip check")
        self.assertIs(cfg["ci_available"], False)


class PerRunOverridesTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(ENV_OVERRIDES, None)

    def _ws(self, files):
        """-> a fresh workspace dir holding {name: text} files."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, text in files.items():
            with open(os.path.join(tmp.name, name), "w", encoding="utf-8") as f:
                f.write(text)
        return tmp.name

    def _write(self, ws, name, text):
        with open(os.path.join(ws, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_order_is_defaults_then_pipeline_json_then_local_then_env(self):
        ws = self._ws({"pipeline.json": json.dumps({"x": "json", "y": "json"}), LOCAL_CONFIG_FILE: json.dumps({"y": "local", "z": "local"})})
        os.environ[ENV_OVERRIDES] = json.dumps({"x": "env"})
        cfg = project(ws)
        self.assertEqual(cfg["x"], "env")
        self.assertEqual(cfg["y"], "local")
        self.assertEqual(cfg["z"], "local")

    def test_local_file_without_pipeline_json_overlays_defaults(self):
        cfg = project(self._ws({LOCAL_CONFIG_FILE: json.dumps({"ci_available": False})}))
        self.assertIs(cfg["ci_available"], False)

    def test_local_file_change_shows_at_once(self):
        ws = self._ws({LOCAL_CONFIG_FILE: json.dumps({"y": "aa"})})
        self.assertEqual(project(ws)["y"], "aa")
        self._write(ws, LOCAL_CONFIG_FILE, json.dumps({"y": "b"}))
        self.assertEqual(project(ws)["y"], "b")

    def test_env_change_shows_at_once(self):
        ws = self._ws({})
        os.environ[ENV_OVERRIDES] = json.dumps({"y": "aa"})
        self.assertEqual(project(ws)["y"], "aa")
        os.environ[ENV_OVERRIDES] = json.dumps({"y": "b"})
        self.assertEqual(project(ws)["y"], "b")

    def test_broken_local_file_raises_naming_it(self):
        for text in ("{not json", "[1, 2]"):
            with self.subTest(text=text):
                ws = self._ws({LOCAL_CONFIG_FILE: text})
                with self.assertRaisesRegex(ConfigError, LOCAL_CONFIG_FILE):
                    project(ws)

    def test_broken_env_raises_naming_the_variable(self):
        for text in ("{not json", "[1, 2]"):
            with self.subTest(text=text):
                os.environ[ENV_OVERRIDES] = text
                with self.assertRaisesRegex(ConfigError, ENV_OVERRIDES):
                    project(self._ws({}))

    def test_docs_impact_gate_off_via_env_leaves_pipeline_json(self):
        ws = self._ws({"pipeline.json": json.dumps({DOCS_IMPACT_KEY: True})})
        os.environ[ENV_OVERRIDES] = json.dumps({DOCS_IMPACT_KEY: False})
        cfg = project(ws)
        self.assertFalse(docs_impact.gate_on(cfg[DOCS_IMPACT_KEY], "any-stage"))
        with open(os.path.join(ws, "pipeline.json"), encoding="utf-8") as f:
            self.assertIs(json.load(f)[DOCS_IMPACT_KEY], True)

    def test_unrelated_env_var_is_ignored(self):
        ws = self._ws({"pipeline.json": json.dumps({"y": "json"})})
        os.environ["PIPELINE_OVERRIDES_DECOY"] = json.dumps({"y": "decoy"})
        self.assertEqual(project(ws)["y"], "json")

    def test_gitignore_lists_the_local_file(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        with open(os.path.join(repo, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(LOCAL_CONFIG_FILE, [line.strip() for line in f])


if __name__ == "__main__":
    unittest.main()
