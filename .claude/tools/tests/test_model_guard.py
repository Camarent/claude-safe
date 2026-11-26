import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import model_guard

GUARD = os.path.abspath(model_guard.__file__)


def run_hook(stdin_text):
    return subprocess.run([sys.executable, "-I", GUARD], check=False, input=stdin_text, capture_output=True, text=True, timeout=30)


class ModelGuardTest(unittest.TestCase):
    def test_denies_missing_model(self):
        self.assertIn("Set `model`", model_guard.verdict({"subagent_type": "dev", "prompt": "x"}))

    def test_allows_haiku(self):
        self.assertEqual(model_guard.verdict({"subagent_type": "ops", "model": "haiku", "prompt": "x"}), "")

    def test_allows_haiku_spelling_variants(self):
        for model in ["haiku ", " Haiku", "claude-haiku-4", "claude-haiku-5-5"]:
            with self.subTest(model=model):
                self.assertEqual(model_guard.verdict({"subagent_type": "dev", "model": model, "prompt": "x"}), "")

    def test_denies_sonnet_without_reason(self):
        self.assertIn("MODEL-REASON", model_guard.verdict({"subagent_type": "general-purpose", "model": "sonnet", "prompt": "transcribe pages"}))

    def test_allows_sonnet_with_reason(self):
        p = "job\nMODEL-REASON: writes a new diff script with parsing logic\n"
        self.assertEqual(model_guard.verdict({"subagent_type": "dev", "model": "sonnet", "prompt": p}), "")

    def test_denies_sonnet_for_ops_even_with_reason(self):
        p = "MODEL-REASON: long enough reason text here"
        self.assertIn("mechanical", model_guard.verdict({"subagent_type": "ops", "model": "sonnet", "prompt": p}))

    def test_fork_gets_the_same_rules(self):
        self.assertIn("Set `model`", model_guard.verdict({"subagent_type": "fork", "prompt": "x"}))
        self.assertIn("MODEL-REASON", model_guard.verdict({"subagent_type": "fork", "model": "sonnet", "prompt": "x"}))
        self.assertEqual(model_guard.verdict({"subagent_type": "fork", "model": "haiku", "prompt": "x"}), "")

    def test_short_reason_rejected(self):
        self.assertIn("MODEL-REASON", model_guard.verdict({"subagent_type": "dev", "model": "opus", "prompt": "MODEL-REASON: ok"}))

    def test_non_string_model_is_malformed(self):
        self.assertIn("string", model_guard.verdict({"subagent_type": "dev", "model": ["haiku"], "prompt": "x"}))

    def test_deny_messages_have_no_hardcoded_root_path(self):
        # messages must not embed a literal install path such as /root/.claude; the hook path lives in settings only
        cases = [
            {"subagent_type": "dev", "prompt": "x"},
            {"subagent_type": "ops", "model": "sonnet", "prompt": "MODEL-REASON: long enough reason text here"},
            {"subagent_type": "dev", "model": "sonnet", "prompt": "transcribe pages"},
            {"subagent_type": "dev", "model": ["haiku"], "prompt": "x"},
        ]
        for tool_input in cases:
            with self.subTest(tool_input=tool_input):
                text = model_guard.verdict(tool_input)
                self.assertTrue(text)
                self.assertNotIn("/root/.claude", text)

    def test_main_denies_missing_model_on_stdout(self):
        r = run_hook(json.dumps({"tool_input": {"subagent_type": "dev", "prompt": "x"}}))
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("Set `model`", out["permissionDecisionReason"])

    def test_main_non_string_model_exits_2_with_reason(self):
        r = run_hook(json.dumps({"tool_input": {"subagent_type": "dev", "model": 5, "prompt": "x"}}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("string", r.stderr)

    def test_main_non_object_tool_input_exits_2(self):
        r = run_hook(json.dumps({"tool_input": "haiku"}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("object", r.stderr)

    def test_main_allows_haiku_silently(self):
        r = run_hook(json.dumps({"tool_input": {"subagent_type": "ops", "model": "haiku", "prompt": "x"}}))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_main_bad_json_allows(self):
        r = run_hook("not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
