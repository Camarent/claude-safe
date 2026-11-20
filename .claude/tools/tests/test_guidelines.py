"""guidelines.detect: language slugs from marker files in a synthetic repo root."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.guidelines import detect


class DetectTest(unittest.TestCase):
    def repo(self, *files):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        for f in files:
            p = os.path.join(t.name, f)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()
        return t.name

    def test_each_marker(self):
        cases = {
            "cpp": ["CMakeLists.txt", "meson.build", "a.cpp", "a.cc", "a.hpp", "src/a.cpp"],
            "rust": ["Cargo.toml"],
            "typescript": ["tsconfig.json"],
            "js": ["package.json"],
            "python": ["pyproject.toml", "setup.py", "requirements.txt"],
            "csharp": ["x.csproj", "x.sln"],
        }
        for lang, files in cases.items():
            for f in files:
                with self.subTest(lang=lang, file=f):
                    self.assertEqual(detect(self.repo(f)), [lang])

    def test_ts_and_js_both(self):
        self.assertEqual(detect(self.repo("tsconfig.json", "package.json")), ["js", "typescript"])

    def test_empty_repo(self):
        self.assertEqual(detect(self.repo()), [])

    def test_no_deep_walk(self):
        self.assertEqual(detect(self.repo("sub/Cargo.toml", "src/deep/a.cpp")), [])
