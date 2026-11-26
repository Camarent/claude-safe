"""Guard: only the CLI parser modules may build argparse.Namespace; the rest of pipeline/ uses Run."""

import re
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
PARSER_MODULES = {"cli_orch.py", "cli_plan.py"}
BUILDS = re.compile(r"\bNamespace\(")


class NoNamespace(unittest.TestCase):
    def test_pipeline_builds_no_namespace(self):
        bad = [p.name for p in sorted(PIPELINE.glob("*.py")) if p.name not in PARSER_MODULES and BUILDS.search(p.read_text())]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
