"""retro-m25-followup docs: scripts.md names each changed behaviour and its code file; decisions.md records the followup."""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPTS_DOC = os.path.join(ROOT, "docs", "modules", "scripts.md")
DECISIONS_DOC = os.path.join(ROOT, "docs", "decisions.md")

ARCHITECTURE_DOC = os.path.join(ROOT, "docs", "ARCHITECTURE.md")

SCRIPTS_SECTION = "## Followup behaviour (retro-m25-followup)"
DECISIONS_SECTION = "## Retro m25 followup (retro-m25-followup)"
STAGE_DECISIONS_SECTION = "## Stage retro-m25-followup2"

# the four plan check modules of the pipeline section of docs/ARCHITECTURE.md
PLAN_CHECK_MODULES = [
    "plan_testroot.py",
    "plan_testwrap.py",
    "plan_docs_impact.py",
    "plan_needs.py",
]

# one name per changed behaviour: its code file (or setting) must appear in the followup section
SCRIPTS_NAMES = [
    "plan_ptest.py",
    "plan_rules.py",
    "cite_rewrite.py",
    "cli_feature.py",
    "configuration/project.py",
    "pipeline.local.json",
    "PIPELINE_OVERRIDES",
    "qrun.py",
    "phases.py",
    "pr_ops.py",
    "verdicts.py",
    "steps.py",
    "branches.py",
    "gate_slots.py",
    "testrun.py",
    "steps_history.py",
    "steps_finaltest.py",
    "GATE_KIND_FLAKE",
    "plan_testroot.py",
    "plan_testwrap.py",
    "plan_docs_impact.py",
    "plan_needs.py",
    "test_roots",
    "test_wrap",
    "roadmap_file",
    "worktree_requirements",
    "retry_note.py",
    "merge_wave.py",
    "cli_orch.py",
    "plan_gaps_open.py",
]

DECISIONS_TERMS = [
    "Override order",
    "gate_slots",
    "wave width",
    ".claude/tools/retro/efficiency-audit.txt",
    "Verify",
    "kata",
]

MACHINE_COUNT = re.compile(r"\b\d+ (?:tests?|modules?|lines?|files?|slots?|tasks?)\b")
LINE_CITE = re.compile(r":\d+\b")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _section(text, heading):
    """The text from `heading` to the next level-2 heading (or the end)."""
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    return text[start:] if nxt < 0 else text[start:nxt]


class DocsFollowupScripts(unittest.TestCase):
    def setUp(self):
        self.section = _section(_read(SCRIPTS_DOC), SCRIPTS_SECTION)

    def test_each_changed_behaviour_names_its_code_file(self):
        for name in SCRIPTS_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, self.section)

    def test_each_changed_behaviour_is_one_paragraph(self):
        bullets = [ln for ln in self.section.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(bullets), 25)  # one bullet per changed behaviour; the last is the plan-review gaps warning

    def test_no_machine_counts_added(self):
        self.assertIsNone(MACHINE_COUNT.search(self.section))

    def test_no_line_reference_cites_added(self):
        self.assertIsNone(LINE_CITE.search(self.section))

    def test_each_cited_code_path_exists(self):
        for path in re.findall(r"\.claude/tools/[\w./-]+\.py", self.section):
            with self.subTest(path=path):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, path)), path)


class DocsFollowupArchitecture(unittest.TestCase):
    def test_plan_check_modules_listed_in_pipeline_section(self):
        text = _read(ARCHITECTURE_DOC)
        for name in PLAN_CHECK_MODULES:
            with self.subTest(name=name):
                self.assertIn(name, text)


class DocsFollowupDecisions(unittest.TestCase):
    def setUp(self):
        self.section = _section(_read(DECISIONS_DOC), DECISIONS_SECTION)

    def test_decisions_record_the_followup_terms(self):
        for term in DECISIONS_TERMS:
            with self.subTest(term=term):
                self.assertIn(term, self.section)

    def test_verify_step_is_owner_or_lead_and_not_run_here(self):
        self.assertIn("owner or lead step, not run by this stage", self.section)

    def test_stage_entry_records_needs_roadmap_and_cost_finding(self):
        text = _read(DECISIONS_DOC)
        stage = _section(text, STAGE_DECISIONS_SECTION)
        for term in ["Needs", "roadmap_file", "docs/cost-lp-nav.md"]:
            with self.subTest(term=term):
                self.assertIn(term, stage)
