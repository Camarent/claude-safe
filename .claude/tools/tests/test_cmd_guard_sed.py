"""cmd_guard `sed -n`: every print-only script is allowed (addresses N, N,M, $, /re/, N~M, several -e scripts); every edit or write form stays denied."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmd_guard

SED_DENY = "`sed -n` is allowed only to print lines"


class SedPrintAllowedTest(unittest.TestCase):
    def test_print_addresses_allowed(self):
        for ok in (
            "sed -n '5p' f",  # line N
            "sed -n '1,5p' f",  # range N,M
            "sed -n '3,$p' f",  # to last line
            "sed -n '$p' f",  # last line
            "sed -n '/re/p' f",  # regex address
            "sed -n '/a/,/b/p' f",  # regex range
            "sed -n '0~2p' f",  # step N~M
            "sed -n 'p' f",  # every line
            "sed -n '1p' a b",  # several files
            "ls | sed -n '1,9p'",  # piped
        ):
            self.assertEqual(cmd_guard.verdict(ok), "", ok)

    def test_several_print_scripts_allowed(self):
        for ok in (
            "sed -n -e '1p' -e '5,7p' f",  # several -e print scripts
            "sed -n -e '1p' -e '/x/p' -e '$p' f",  # mixed addresses, one -e each
            "sed -n -e '/a/,/b/p' -e 2p f",  # regex range and a line
            "sed -n '/^ab c/p' f",  # anchor and quoted space in a regex address
            "sed -n --quiet '2p' f",  # long quiet option, print script
        ):
            self.assertEqual(cmd_guard.verdict(ok), "", ok)


class SedEditDeniedTest(unittest.TestCase):
    def test_edit_and_write_forms_denied(self):
        for bad in (
            "sed -n '1w x' f",  # w command
            "sed -n '1,5p;w x' f",  # w after print
            "sed -n -e '1p' -e '2w x' f",  # w in a second -e
            "sed -n '1e date' f",  # e command
            "sed -n '1r x' f",  # r command
            "sed -n '1s/a/b/p' f",  # s command
            "sed -n '1p' -i f",  # -i after the script
            "sed -n -i '1p' f",  # -i before the script
            "sed -n --in-place '1p' f",  # long in-place option
            "sed -n -f s.sed f",  # script file
            "sed -n '1p' -s f",  # other option
            "sed -n '1p # w x' f",  # comment hiding a write
            "sed -n 'w x' f",  # no address
            "sed -n '1,2p ;w x' f",  # space before ; hides a write from a whitespace split
            "sed -n '1p\nw x' f",  # newline-separated write
            "sed -n '1p;5p' f",  # two commands in one script: denied, use two -e
            "sed -n -e '1p;w x' f",  # write after a print in one -e script
            "sed -n '1p' ; sed -n 'w x' f",  # second sed segment is checked too
            "sed -n '1p' f \"",  # unbalanced quote on a sed call
            "sed -n /$(id)/p f",  # command substitution in a regex address runs before sed
            "sed -n /`id`/p f",  # backtick substitution in a regex address
            "sed -n /a*/p f",  # glob in a regex address expands on disk
            "sed -ne 'w x' f",  # clustered -ne hides the quiet flag and the write
            "sed -n --quiet 'w x' f",  # long quiet option, write script
            "sed -s -n 'w x' f",  # other option before -n
        ):
            self.assertIn(SED_DENY, cmd_guard.verdict(bad), bad)

    def test_sed_without_n_not_checked_here(self):
        # `sed -i` is not opened by the default allowlist; a project's own `sed *` rule decides
        self.assertEqual(cmd_guard.verdict("sed -i 's/a/b/' f"), "")


if __name__ == "__main__":
    unittest.main()
