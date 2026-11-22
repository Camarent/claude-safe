"""The tuning settings of the stage are owned by configuration/tuning.py and importable from the pipeline.configuration facade."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.configuration import (
    DENIAL_LOG_NAME,
    ENV_ERROR_PATTERN,
    ENV_REPAIR_MAX,
    GATE_SKIP_PATHS,
    HAIKU_REWORK_TRIES,
    LEAD_SLICE_CHARS_WITH_PATTERN,
    MERGE_RESIDUE_FILE,
    PGID_FILE_NAME,
    REVIEW_DOCS_MAX_TURNS,
    STALE_RERUN_MAX,
)


class TuningSettings(unittest.TestCase):
    def test_values(self):
        self.assertEqual(
            ENV_ERROR_PATTERN,
            r"No module named|ModuleNotFoundError|no config path|command not found",
        )
        self.assertEqual(ENV_REPAIR_MAX, 1)
        self.assertEqual(STALE_RERUN_MAX, 2)
        self.assertEqual(GATE_SKIP_PATHS, ("*.md",))
        self.assertEqual(LEAD_SLICE_CHARS_WITH_PATTERN, 3000)
        self.assertEqual(HAIKU_REWORK_TRIES, 1)
        self.assertEqual(REVIEW_DOCS_MAX_TURNS, 12)

    def test_file_names_are_plain_names(self):
        for name in (DENIAL_LOG_NAME, PGID_FILE_NAME, MERGE_RESIDUE_FILE):
            self.assertIsInstance(name, str)
            self.assertTrue(name)
            self.assertNotIn(os.sep, name)


if __name__ == "__main__":
    unittest.main()
