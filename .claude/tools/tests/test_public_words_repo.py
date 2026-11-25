"""public-words gate on this repo: the word list in pipeline.json, the empty baseline, a clean scan of the tracked files, and the skip list."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import configuration, public_words

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# Split literals: this file is itself tracked, so the scan would find the words written whole.
EXPECTED_WORDS = (
    "ka" + "ta",
    "do" + "jo",
    "model-" + "host",
    "model-" + "net",
    "strat" + "a.key",
    "prox" + "mox",
    "tail" + "scale",
    "wire" + "guard",
    os.sep + "home" + os.sep,
    "tit" + "ov",
    "kir" + "ill",
)


def _load_pipeline():
    with open(os.path.join(REPO, "pipeline.json"), encoding="utf-8") as f:
        return json.load(f)


class T(unittest.TestCase):
    def test_c1_pipeline_json_lists_every_expected_word(self):
        words = _load_pipeline()[configuration.PUBLIC_FORBIDDEN_WORDS_KEY]
        for word in EXPECTED_WORDS:
            self.assertIn(word, words)

    def test_c2_baseline_has_no_hit_line(self):
        self.assertEqual(public_words.read_baseline(os.path.join(REPO, configuration.PUBLIC_WORDS_BASELINE_DEFAULT)), set())

    def test_c3_tracked_files_have_no_forbidden_word(self):
        data = _load_pipeline()
        words = data[configuration.PUBLIC_FORBIDDEN_WORDS_KEY]
        skip = data.get(configuration.PUBLIC_WORDS_SKIP_FILES_KEY, configuration.PUBLIC_WORDS_SKIP_FILES_DEFAULT)
        self.assertEqual(public_words.records(REPO, words, skip), [])

    def test_c4_mutation_tracked_word_is_reported(self):
        word = "ka" + "ta"
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "-C", td, "init", "-q"], check=True, capture_output=True)
            with open(os.path.join(td, "notes.txt"), "w", encoding="utf-8") as f:
                f.write(f"one line with {word} in it\n")
            subprocess.run(["git", "-C", td, "add", "-A"], check=True, capture_output=True)
            found = public_words.records(td, [word])
        self.assertEqual([(rel, n, w) for rel, n, w, _text in found], [("notes.txt", 1, word)])

    def test_c5_skip_list_excludes_the_word_list_file(self):
        word = "ka" + "ta"
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "-C", td, "init", "-q"], check=True, capture_output=True)
            with open(os.path.join(td, "pipeline.json"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"public_forbidden_words": [word]}))
            subprocess.run(["git", "-C", td, "add", "-A"], check=True, capture_output=True)
            self.assertEqual(public_words.records(td, [word], configuration.PUBLIC_WORDS_SKIP_FILES_DEFAULT), [])
            self.assertEqual([rel for rel, *_ in public_words.records(td, [word])], ["pipeline.json"])

    def test_skip_default_is_the_pipeline_json_file(self):
        self.assertEqual(configuration.PUBLIC_WORDS_SKIP_FILES_KEY, "public_words_skip_files")
        self.assertEqual(configuration.PUBLIC_WORDS_SKIP_FILES_DEFAULT, ["pipeline.json"])
