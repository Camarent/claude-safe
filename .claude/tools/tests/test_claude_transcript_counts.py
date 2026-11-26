"""claude.tool_counts / search_counts: parse a session transcript under ~/.claude/projects."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude
from tests.support.tmpdirs import tracked_mkdtemp


def _use(name, **inp):
    return {"message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


class Counts(unittest.TestCase):
    def setUp(self):
        self.home = tracked_mkdtemp()
        os.makedirs(f"{self.home}/.claude/projects/p")
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)

    def _write(self, sid, rows, raw=()):
        with open(f"{self.home}/.claude/projects/p/{sid}.jsonl", "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            for x in raw:
                fh.write(x + "\n")

    def test_tool_counts_counts_tool_use_blocks_and_skips_bad_lines(self):
        self._write(
            "abc-1", [_use("Read", file_path="a"), _use("Read", file_path="b"), _use("Bash", command="ls"), {"message": {"content": "text"}}], raw=["not json"]
        )
        self.assertEqual(claude.tool_counts("abc-1"), {"Read": 2, "Bash": 1})

    def test_missing_or_unsafe_session_id_gives_empty(self):
        self.assertEqual(claude.tool_counts("nope"), {})
        self.assertEqual(claude.tool_counts(None), {})
        self.assertEqual(claude.tool_counts("../x"), {})
        self.assertEqual(claude.search_counts("../x"), {})

    def test_search_counts_separates_grep_from_other_tools(self):
        self._write("abc-2", [_use("Grep", pattern="x"), _use("Grep", pattern="y"), _use("Read", file_path="a")])
        got = claude.search_counts("abc-2")
        self.assertEqual(got.get("grep"), 2)
        self.assertNotIn("read", got)


if __name__ == "__main__":
    unittest.main()
