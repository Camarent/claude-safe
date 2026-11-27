"""denials: family labels and a synthetic transcript scan."""

import datetime
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import denial_log
from pipeline import claude
from pipeline.configuration import ENV_DENIAL_SESSION, ENV_SCRATCH_DIR
from retro import denials
from tests.support.tmpdirs import tracked_mkdtemp

SNIP = '"/usr/local/bin/snip" run -- '
MULTI = "This Bash command contains multiple operations. The following part requires approval: "


class Family(unittest.TestCase):
    def test_labels(self):
        f = denials.family
        self.assertEqual(f("This command changes directory before running a version-control command, which can pick up untrusted hooks...", ""), "cd-then-git")
        self.assertEqual(f(MULTI + "cd /w/x && " + SNIP + "git -c safe.directory='*' status", ""), "multi: cd&&git")
        self.assertEqual(f(MULTI + "cd /w", ""), "multi: cd&&other")
        self.assertEqual(f(MULTI + SNIP + "git status", ""), "multi: git part")
        self.assertEqual(f(MULTI + "make all", ""), "multi: other (make all)")
        self.assertEqual(f("This command requires approval", "rm -rf x"), "approval: rm")
        self.assertEqual(f("This command requires approval", "FOO=1 rm x"), "approval: ENV-prefix rm")
        self.assertEqual(f("sed command requires approval (contains potentially dangerous operations)", ""), "sed")
        self.assertEqual(f("No pipe allowed", ""), "qrun-chain hook")
        self.assertEqual(f("Reviewer sessions may not", ""), "reviewer guard")
        self.assertEqual(f("blocked: redirection", ""), "cd+redirect")
        self.assertEqual(f("weird blocked thing", ""), "other: weird blocked thing")


class Scan(unittest.TestCase):
    def test_scan(self):
        d = tracked_mkdtemp()
        os.mkdir(os.path.join(d, "p"))
        ts = datetime.date.today().isoformat() + "T10:00:00.000Z"

        def use(i, cmd, entry="sdk-cli"):
            blk = {"type": "tool_use", "name": "Bash", "id": i, "input": {"command": cmd}}
            return {"entrypoint": entry, "timestamp": ts, "message": {"content": [blk]}}

        def res(i, text, err=True):
            blk = {"type": "tool_result", "tool_use_id": i, "is_error": err, "content": text}
            return {"entrypoint": "sdk-cli", "timestamp": ts, "message": {"content": [blk]}}

        evs = [
            use("1", "rm x"),
            res("1", "This command requires approval"),
            use("2", "ls"),
            res("2", [{"text": "ok"}], False),
            use("3", "sed -i x"),
            res("3", [{"text": "sed command requires approval"}]),
        ]
        with open(os.path.join(d, "p", "a.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(e) for e in evs) + "\nnot json\n")
        with open(os.path.join(d, "p", "b.jsonl"), "w") as f:
            f.write(json.dumps(use("9", "ls", "cli")) + "\n")
        calls, den = denials.collect(d, 5)
        s = denials.summarize(calls, den)
        self.assertEqual((s["bash_calls"], s["denied"]), (3, 2))
        self.assertEqual({x["family"] for x in s["families"]}, {"approval: rm", "sed"})
        self.assertIn("denied: 2", denials.render(s, 1))


class Fix(unittest.TestCase):
    def test_multi_and_classifier_get_fix_line(self):
        self.assertEqual(denials.fix_for(denials.family(MULTI + "make all", "")), denials.MULTI_FIX)
        self.assertEqual(denials.fix_for(denials.family(MULTI + "cd /w", "")), denials.MULTI_FIX)
        classifier = denials.family("The classifier blocked this compound command", "")
        self.assertEqual(denials.fix_for(classifier), denials.CLASSIFIER_FIX)

    def test_unknown_family_prints_no_fix(self):
        self.assertEqual(denials.fix_for(denials.family("weird blocked thing", "")), "")
        self.assertEqual(denials.fix_for("sed"), "")

    def test_render_prints_fix_under_family(self):
        s = {
            "bash_calls": 4,
            "denied": 3,
            "rate": 0.75,
            "per_day": {},
            "families": [
                {"family": "multi: other (make all)", "count": 2, "share": 2 / 3, "example": "make all"},
                {"family": "sed", "count": 1, "share": 1 / 3, "example": "sed -i x"},
            ],
        }
        out = denials.render(s, 0).splitlines()
        i = out.index(f"  {2:5d}  {100 * 2 / 3:5.1f}%  multi: other (make all)")
        self.assertIn("fix: " + denials.MULTI_FIX, out[i + 1])
        self.assertFalse(any("fix:" in line for line in out[i + 2 :]))


class DeniedGuard(unittest.TestCase):
    def test_guard_name_ends_each_entry_in_order(self):
        den = [
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}, "reason": "no rm"},
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            {"tool_name": "Write", "tool_input": {"file_path": "/a/b"}},
        ]
        guards = [("rm -rf x", "guard"), ("ls", "main_checkout")]
        self.assertEqual(
            claude.denial_lines(den, guards),
            ["Bash: rm -rf x [no rm] guard", "Bash: ls main_checkout", "Write: /a/b"],
        )

    def test_each_row_used_once(self):
        den = [{"tool_name": "Bash", "tool_input": {"command": "ls"}}, {"tool_name": "Bash", "tool_input": {"command": "ls"}}]
        self.assertEqual(claude.denial_lines(den, [("ls", "guard")]), ["Bash: ls guard", "Bash: ls"])

    def test_no_guards_no_suffix(self):
        den = [{"tool_name": "Bash", "tool_input": {"command": "ls"}}]
        self.assertEqual(claude.denial_lines(den), ["Bash: ls"])

    def test_empty_row_command_matches_nothing(self):
        den = [{"tool_name": "Bash", "tool_input": {"command": "ls"}}, {"tool_name": "Write", "tool_input": {"file_path": "/a"}}]
        self.assertEqual(claude.denial_lines(den, [("", "review")]), ["Bash: ls", "Write: /a"])

    def test_prefix_but_not_equal_command_matches_nothing(self):
        den = [{"tool_name": "Bash", "tool_input": {"command": "lsof -p 1"}}]
        self.assertEqual(claude.denial_lines(den, [("ls", "guard")]), ["Bash: lsof -p 1"])


class SessionRows(unittest.TestCase):
    def test_only_this_sessions_rows_name_its_denials(self):
        scratch = tracked_mkdtemp()
        den = [{"tool_name": "Bash", "tool_input": {"command": "ls"}}, {"tool_name": "Bash", "tool_input": {"command": "rm x"}}]

        def fake_run(args, cwd, timeout, env_extra, **kw):
            # a sibling session's hook row lands first, then this session's own row
            with open(os.path.join(scratch, denial_log.DENIALS_FILE), "a", encoding="utf-8") as f:
                f.write(json.dumps({"rule": "sibling", "command": "ls", "session": "other"}) + "\n")
                f.write(json.dumps({"rule": "guard", "command": "rm x", "session": env_extra[ENV_DENIAL_SESSION]}) + "\n")
            return {"permission_denials": den}

        with mock.patch.object(claude, "run_claude", fake_run):
            res = claude.session(["claude"], scratch, env_extra={ENV_SCRATCH_DIR: scratch})
        self.assertEqual(res["denials"], ["Bash: ls", "Bash: rm x guard"])


if __name__ == "__main__":
    unittest.main()
