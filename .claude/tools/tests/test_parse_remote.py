import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_mode

CASES = (
    ("https://github.com/o/r.git", ("github.com", "o/r")),
    ("https://github.com/o/r", ("github.com", "o/r")),
    ("https://github.com/o/r/", ("github.com", "o/r")),
    ("HTTPS://GitHub.com/o/r.git", ("github.com", "o/r")),
    ("https://user@github.com/o/r.git", ("github.com", "o/r")),
    ("https://github.com:8443/o/r.git", ("github.com", "o/r")),
    ("ssh://git@github.com/o/r.git", ("github.com", "o/r")),
    ("ssh://git@host.example:2222/o/r.git", ("host.example", "o/r")),
    ("git@gitlab.com:o/r.git", ("gitlab.com", "o/r")),
    ("git@gitlab.com:o/r", ("gitlab.com", "o/r")),
    ("gitlab.com:o/r.git", ("gitlab.com", "o/r")),
    ("https://github.com/o", ("", "")),
    ("https://github.com/a/b/c", ("", "")),
    ("git@gitlab.com:o", ("", "")),
    ("", ("", "")),
    ("localpath", ("", "")),
)


class ParseRemoteTest(unittest.TestCase):
    def test_table(self):
        for url, want in CASES:
            with self.subTest(url=url):
                self.assertEqual(pr_mode.parse_remote(url), want)


if __name__ == "__main__":
    unittest.main()
