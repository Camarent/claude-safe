import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "localmodel"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import safeshell as ss
from pipeline.shellwords import short_flag


class Check(unittest.TestCase):
    def assert_ok(self, cmd):
        self.assertIsNone(ss.check(cmd), cmd)

    def assert_bad(self, cmd):
        self.assertIsNotNone(ss.check(cmd), cmd)

    def test_allowed(self):
        for c in [
            "git ls-files | head -50",
            "grep -rn foo src 2>/dev/null | head -20",
            "sed -n '1,60p' a.txt",
            "ls a && ls b || echo none",
            "git log --name-only -n 5 -- a.py",
            "lp nav refs foo",
            'lp find "where is x" --no-model',
            "find . -name '*.md' | sort | uniq | wc -l",
            "cat a.txt 2>&1 | tail -5",
            "echo 'a > b'",
            "jq .a x.json",
        ]:
            self.assert_ok(c)

    def test_sed_words_with_r_w_e_are_fine(self):
        for c in ("sed -n '/error/p' a.txt", "sed 's/a/replace/' a.txt", "sed -n 's/the end/x/p' a.txt", "sed", "sed -n"):
            self.assert_ok(c)
        for c in ("sed 's/a/b/w out' a.txt", "sed 's/a/b/e' a.txt", "sed -n '1r x' a.txt"):
            self.assert_bad(c)

    def test_refused(self):
        for c in [
            "curl http://x",
            "rm -rf a",
            "python3 -c 'print(1)'",
            "cat a > b",
            "echo x >> b",
            "cat < a",
            "echo $(ls)",
            "echo `ls`",
            "cat <<EOF",
            "sed -i s/a/b/ f",
            "sed -n 'w out' f",
            "sed 's/a/b/e' f",
            "find . -exec rm {} ;",
            "find . -delete",
            "git push",
            "git commit -m x",
            "git -c core.pager=x log",
            "git log --output=x",
            "lp index",
            "cat /workspace/x",
            "cat ../x",
            "ls /etc",
            "cd a; rm b",
            "grep a b | tee c",
            "ls\nrm a",
            "git",
            "",
        ]:
            self.assert_bad(c)

    def test_bypasses_refused(self):
        for c in [
            "ls |& rm -rf x",
            "cat {,/}etc/passwd",
            "cat {a,b}",
            "cat ~root/x",
            "cat ~/x",
            "grep --file=/etc/passwd .",
            "grep -f x .",
            "grep -rf x .",
            "grep --file x .",
            "sed 'r /etc/passwd' f",
            "sed '1r /etc/passwd' f",
            "sed '/a/r x' f",
            "sed 's/a/b/w out' f",
            "sed 's/a/b/gw out' f",
            "sed --in-place=.bak -n p f",
            "cat /run/secrets/x",
            "cat /srv/x",
            "git grep -Osh x",
            "git grep --open-files-in-pager=vim x",
            "git grep -O x",
            "find -fprint0 x",
            "find . -fprint x",
            "sort -o x y",
            "sort --output=x y",
            "uniq a b",
            "cat 'a",
        ]:
            self.assert_bad(c)

    def test_tokens_that_are_harmless(self):
        for c in ["> /dev/null", "tail -f a.txt | head -1", "find . -follow -name x", "cut -f1 a.txt", "sort -n a.txt"]:
            self.assert_ok(c)

    def test_run(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.txt"), "w") as fh:
                fh.write("one\ntwo\n")
            self.assertEqual(ss.run("sed -n '2p' a.txt", d), "two")
            self.assertTrue(ss.run("rm a.txt", d).startswith("error:"))
            self.assertTrue(os.path.exists(os.path.join(d, "a.txt")))
            self.assertEqual(ss.run("grep zzz a.txt", d), "(no result)")
            self.assertEqual(ss.run("tail -f a.txt", d, timeout=1), "error: timeout 1 s")
            self.assertEqual(ss.run("cat a.txt", d), "one\ntwo")


class Helpers(unittest.TestCase):
    ARGS = ["-f", "-rnf", "-nO", "-x", "--file", "--output=x", "-", "--", "-1f", "-o", "-so", "foo", "f", "-Of", ""]

    def test_short_flag_matches_old_regex(self):
        import re

        for letters in ("f", "O", "o", "fO"):
            for a in self.ARGS:
                self.assertEqual(short_flag(a, letters), bool(re.match(rf"-[A-Za-z]*[{letters}]", a)), (a, letters))

    def test_sed_script_writes(self):
        for s, want in {
            "p": False,
            "1,3p": False,
            "s/a/b/": False,
            "s/a/b/w out": True,
            "w out": True,
            "r f": True,
            "/x/{p;w o}": True,
            "$!e": True,
            "s/a/b/e": True,
            "s/a/b/gp": False,
            "1d": False,
        }.items():
            self.assertEqual(ss.sed_script_writes(s), want, s)


if __name__ == "__main__":
    unittest.main()
