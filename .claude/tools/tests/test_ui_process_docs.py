import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import tracked_mkdtemp

# Rule words of the UI process (planning.md step list, architect, ux-reviewer, web-tester).
RULE_WORDS = ["glossary", "journeys", "prototype", "rendered review", "sign-off"]
PLANNING = ".claude/practices/planning.md"
ARCHITECT = ".claude/agents/architect.md"
UX_REVIEWER = ".claude/agents/ux-reviewer.md"
WEB_TESTER = ".claude/agents/web-tester.md"
# Files that must carry every rule word.
FULL_FILES = [PLANNING, ARCHITECT]
# Files that must carry at least one rule word, and "rendered review".
PAIR_FILES = [UX_REVIEWER, WEB_TESTER]


def find_root(start):
    """Walk up from START to the directory holding the planning doc (the project root)."""
    path = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(path, PLANNING)):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            raise FileNotFoundError(f"{PLANNING} not found above {start}")
        path = parent


def missing_words(text, words):
    low = text.lower()
    return [w for w in words if w not in low]


def check_docs(root):
    """Return problem strings 'relpath: missing ...'; empty when every rule holds."""
    problems = []
    for rel in FULL_FILES:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            text = f.read()
        for word in missing_words(text, RULE_WORDS):
            problems.append(f"{rel}: missing word '{word}'")
    for rel in PAIR_FILES:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            text = f.read()
        if len(missing_words(text, RULE_WORDS)) == len(RULE_WORDS):
            problems.append(f"{rel}: missing every rule word {RULE_WORDS}")
        for word in missing_words(text, ["rendered review"]):
            problems.append(f"{rel}: missing word '{word}'")
    return problems


class UiProcessDocsTest(unittest.TestCase):
    def setUp(self):
        self.root = find_root(os.path.dirname(os.path.abspath(__file__)))

    def test_rule_words_present_in_process_docs(self):
        self.assertEqual([], check_docs(self.root))

    def test_missing_word_is_reported_with_file_and_word(self):
        scratch = tracked_mkdtemp(prefix="uidocs-")
        self.addCleanup(shutil.rmtree, scratch, True)
        for rel in FULL_FILES + PAIR_FILES:
            dst = os.path.join(scratch, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(os.path.join(self.root, rel), encoding="utf-8") as f:
                text = f.read()
            if rel == PLANNING:
                text = text.replace("glossary", "").replace("Glossary", "")
            with open(dst, "w", encoding="utf-8") as f:
                f.write(text)
        problems = check_docs(scratch)
        self.assertTrue(
            any(PLANNING in p and "'glossary'" in p for p in problems),
            f"expected planning glossary problem, got {problems}",
        )
        self.assertFalse(any(ARCHITECT in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
