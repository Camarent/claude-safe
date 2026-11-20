"""Pins both heredoc strippers: cmd_guard keeps the `<<WORD` opener, lp_share drops the whole heredoc."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmd_guard
from pipeline import lp_share


class HeredocPinTest(unittest.TestCase):
    def test_guard_keeps_opener(self):
        self.assertEqual(cmd_guard.strip_heredocs("cat <<EOF\ngrep -r x .\nEOF\necho z"), "cat <<EOF\necho z")

    def test_lp_share_drops_whole_heredoc(self):
        body = "cat <<EOF > f\ngrep -r foo src\nEOF"
        self.assertIsNone(lp_share._bash_kind(body))
        self.assertEqual(lp_share._bash_kind(body + "\ngrep -r foo src"), "grep")
        self.assertEqual(lp_share._bash_kind("cat <<'EOF'\nx\nEOF\ngrep -r foo src"), "grep")
        self.assertEqual(lp_share._bash_kind("cat <<-EOF\n\tx\n\tEOF\ngrep -r foo src"), "grep")

    def test_lp_share_unterminated_stays(self):
        self.assertEqual(lp_share._bash_kind("cat <<EOF\ngrep -r foo src"), "grep")


if __name__ == "__main__":
    unittest.main()
