"""AGENT_TUNING_FLAGS (token trim) reach every headless agent except TUNING_EXEMPT_AGENTS (publisher uses Skill)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude
from pipeline.configuration import AGENT_TUNING_FLAGS, ENV_CLAUDE_FLAGS, TUNING_EXEMPT_AGENTS
from tests.support.tmpdirs import tracked_mkdtemp

TUNED_AGENTS = ("dev", "reviewer", "architect", "arch-auditor", "sec-auditor")
# the three flags as the spec names them; pinned here so a dropped entry in flags.py fails
EXPECTED_FLAGS = ("--disable-slash-commands", "--strict-mcp-config", "--exclude-dynamic-system-prompt-sections")


class AgentTuningFlagsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(ENV_CLAUDE_FLAGS, None)  # the default flags, not a WAVE_CLAUDE_FLAGS override

    def test_setting_holds_the_three_flags(self):
        self.assertEqual(AGENT_TUNING_FLAGS, EXPECTED_FLAGS)

    def test_tuned_agents_get_all_three_flags(self):
        for agent in TUNED_AGENTS:
            args = claude.claude_args(agent, None, None, "p", dir=self.d)
            for flag in EXPECTED_FLAGS:
                self.assertIn(flag, args, f"{agent} lacks {flag}")

    def test_publisher_gets_none_of_the_flags(self):
        for agent in TUNING_EXEMPT_AGENTS:
            args = claude.claude_args(agent, None, None, "p", dir=self.d)
            for flag in EXPECTED_FLAGS:
                self.assertNotIn(flag, args, f"{agent} still has {flag}")

    def test_publisher_keeps_the_rest_of_the_base_flags(self):
        args = claude.claude_args("publisher", None, None, "p", dir=self.d)
        self.assertIn("acceptEdits", args)
        self.assertIn("Read", args)

    def test_publisher_strips_flags_from_env_override(self):
        os.environ[ENV_CLAUDE_FLAGS] = "--permission-mode acceptEdits " + " ".join(AGENT_TUNING_FLAGS)
        args = claude.claude_args("publisher", None, None, "p", dir=self.d)
        for flag in AGENT_TUNING_FLAGS:
            self.assertNotIn(flag, args)
        self.assertIn("acceptEdits", args)

    def test_flag_names_come_from_configuration(self):
        self.assertIs(claude.AGENT_TUNING_FLAGS, AGENT_TUNING_FLAGS)
        self.assertIs(claude.TUNING_EXEMPT_AGENTS, TUNING_EXEMPT_AGENTS)

    def test_every_allow_pattern_follows_an_allowedtools_flag(self):
        """A word after another flag would become the prompt; every Bash(...) pattern must sit in an --allowedTools list."""
        for agent in TUNED_AGENTS + TUNING_EXEMPT_AGENTS:
            args = claude.claude_args(agent, None, None, "the-prompt", dir=self.d, scratch=self.d)
            self.assertEqual(args[-2:], ["--", "the-prompt"])
            in_list = False
            for word in args[: args.index("--")]:
                if word == "--allowedTools":
                    in_list = True
                elif word.startswith("--"):
                    in_list = False
                elif word.startswith("Bash("):
                    self.assertTrue(in_list, f"{agent}: {word} is outside an --allowedTools list")


if __name__ == "__main__":
    unittest.main()
