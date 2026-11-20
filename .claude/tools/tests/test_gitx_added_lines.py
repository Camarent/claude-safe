import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx

DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py\t
@@ -1,0 +2,2 @@
+one
+two
@@ -9 +11 @@
-old
+three
diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-x
diff --git a/del.py b/del.py
--- a/del.py
+++ b/del.py
@@ -3 +2,0 @@
-y
"""


class AddedLines(unittest.TestCase):
    def test_lines_numbers_and_keys(self):
        self.assertEqual(gitx.added_lines(DIFF), {"a.py": [(2, "one"), (3, "two"), (11, "three")], "del.py": []})


if __name__ == "__main__":
    unittest.main()
