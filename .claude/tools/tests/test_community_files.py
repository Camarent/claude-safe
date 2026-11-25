"""Community files: license, security policy, contributing guide, code of conduct, changelog and GitHub templates exist with their key headings."""

import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# file -> headings (or phrases) each one must contain, one per line
REQUIRED = {
    "LICENSE": ("MIT License", "Copyright (c) 2026 Camarent"),
    "SECURITY.md": (
        "# Security",
        "## Threat model",
        "## Credentials",
        "## Auto mode",
        "## Reporting a vulnerability",
        "## Supported versions",
    ),
    "CONTRIBUTING.md": (
        "# Contributing",
        "## Dev setup",
        "## Checks",
        "## Branches and commits",
        "## Docs and tests",
        "## Privacy",
        "## License of contributions",
    ),
    "CODE_OF_CONDUCT.md": ("# Code of Conduct", "## Our standard", "## Reporting", "## Enforcement"),
    "CHANGELOG.md": ("# Changelog",),
    ".github/ISSUE_TEMPLATE/bug_report.md": ("name: Bug report", "## Steps to reproduce"),
    ".github/ISSUE_TEMPLATE/feature_request.md": ("name: Feature request", "## Proposed change"),
    ".github/pull_request_template.md": ("## Summary", "## Checks"),
}
RELEASE_HEADING = "## 0.1.0"
PRIVATE_CHANNEL = "private vulnerability reporting"


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _changelog_problems(text):
    """Empty list when the changelog has exactly one 0.1.0 heading."""
    count = sum(1 for line in text.splitlines() if line.strip() == RELEASE_HEADING)
    if count == 1:
        return []
    return [f"expected one '{RELEASE_HEADING}' heading, found {count}"]


class T(unittest.TestCase):
    def test_c1_each_file_exists_with_its_key_headings(self):
        for rel, needles in REQUIRED.items():
            with self.subTest(file=rel):
                self.assertTrue(os.path.isfile(os.path.join(REPO, rel)))
                text = _read(rel)
                for needle in needles:
                    self.assertIn(needle, text)

    def test_c1_license_is_mit_text(self):
        self.assertIn("Permission is hereby granted, free of charge", _read("LICENSE"))

    def test_c1_changelog_has_one_0_1_0_entry(self):
        self.assertEqual(_changelog_problems(_read("CHANGELOG.md")), [])

    def test_c2_security_names_private_advisories_as_channel(self):
        self.assertIn(PRIVATE_CHANNEL, _read("SECURITY.md"))

    def test_c3_mutation_deleted_release_heading_is_reported(self):
        mutated = _read("CHANGELOG.md").replace(RELEASE_HEADING, "## Unreleased", 1)
        self.assertNotEqual(_changelog_problems(mutated), [])

    def test_c3_mutation_duplicate_release_heading_is_reported(self):
        doubled = _read("CHANGELOG.md") + "\n" + RELEASE_HEADING + "\n"
        self.assertNotEqual(_changelog_problems(doubled), [])
