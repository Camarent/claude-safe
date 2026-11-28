"""GitHub Actions pins: every `uses:` in .github/workflows names a full commit SHA, the one recorded for that action."""

import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
WORKFLOWS = os.path.join(REPO, ".github", "workflows", "*.yml")
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.MULTILINE)
SHA_REF = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
# Commit SHA per action, as recorded in the lead's pins table (ops-pins).
EXPECTED = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "astral-sh/setup-uv": "1db35dd8eee11c5894872e0b383ae6c406cf738d",
}


def uses_refs(text):
    return USES.findall(text)


def unpinned(text):
    # Local actions (./path) have no ref; every remote action needs @<40 hex>.
    return [ref for ref in uses_refs(text) if not ref.startswith("./") and not SHA_REF.match(ref)]


def workflow_texts():
    out = {}
    for path in sorted(glob.glob(WORKFLOWS)):
        with open(path, encoding="utf-8") as f:
            out[path] = f.read()
    return out


class WorkflowPinTest(unittest.TestCase):
    def test_every_uses_line_is_a_commit_sha(self):
        texts = workflow_texts()
        self.assertTrue(texts, "no workflow files found")
        for path, text in texts.items():
            with self.subTest(path=path):
                self.assertEqual(unpinned(text), [])

    def test_tag_instead_of_sha_is_reported(self):
        # Mutation: one SHA swapped back to a tag must fail the same check.
        text = "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7\n      - uses: astral-sh/setup-uv@v7\n"
        self.assertEqual(unpinned(text), ["astral-sh/setup-uv@v7"])

    def test_short_sha_is_reported(self):
        self.assertEqual(unpinned("      - uses: actions/checkout@3d3c42e\n"), ["actions/checkout@3d3c42e"])

    def test_ci_sha_matches_recorded_pin(self):
        with open(os.path.join(REPO, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
            text = f.read()
        refs = [r for r in uses_refs(text) if not r.startswith("./")]
        self.assertTrue(refs, "ci.yml has no uses lines")
        for ref in refs:
            action, sha = ref.split("@", 1)
            with self.subTest(action=action):
                self.assertIn(action, EXPECTED)
                self.assertEqual(sha, EXPECTED[action])


if __name__ == "__main__":
    unittest.main()
