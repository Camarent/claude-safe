"""scripts/host_agents.render: frontmatter is kept (even when empty) and only /root/.claude is rewritten (no I/O)."""

import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import host_agents as H  # noqa: E402


class TestRender(unittest.TestCase):
    def test_empty_frontmatter_is_kept(self):
        text, _ = H.render("---\n---\nbody line\n", "/home/u/.claude", "pl-")
        self.assertTrue(text.startswith("---\n---\n"), text)
        self.assertIn("body line", text)

    def test_frontmatter_name_prefixed(self):
        text, _ = H.render("---\nname: dev\n---\nbody\n", "/h", "pl-")
        self.assertEqual(text, "---\nname: pl-dev\n---\nbody\n")

    def test_no_frontmatter_unchanged_head(self):
        text, _ = H.render("body only\n", "/h", "pl-")
        self.assertEqual(text, "body only\n")

    def test_only_container_home_is_rewritten(self):
        body = "read /root/.claude/tools and /workspace/proj\n"
        text, rep = H.render("---\nname: a\n---\n" + body, "/home/u/.claude", "pl-")
        self.assertIn("/home/u/.claude/tools", text)
        self.assertIn("/workspace/proj", text)
        self.assertEqual(rep["paths"], 1)


INDEX = {
    "version": 1,
    "prefix": "cs-",
    "roots": {
        "home": {"container": "/root/.claude", "host": "@home"},
        "workspace": {"container": "/workspace", "host": "@projects_dir"},
        "projects_dir": {"side": "host", "host": "/proj"},
    },
    "commands": {"qrun": {"script": "tools/qrun.py"}},
}
AGENT = "---\nname: a\n---\nrun cs-qrun in /root/.claude and /workspace/x\n"


class TestRenderIndex(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, ".claude")
        os.makedirs(os.path.join(self.home, "bin"))
        self.wrapper = os.path.join(self.home, "bin", "cs-qrun")
        with open(self.wrapper, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        with open(os.path.join(self.home, "paths.json"), "w", encoding="utf-8") as f:
            json.dump(INDEX, f)

    def test_host_render(self):
        text, rep = H.render(AGENT, self.home, "pl-", INDEX)
        self.assertIn(self.wrapper, text)
        self.assertIn("/proj/x", text)
        self.assertNotIn("/root/.claude", text)
        self.assertNotIn("/workspace", text)
        self.assertNotRegex(text.replace(self.wrapper, ""), r"cs-\w")
        self.assertEqual(rep["commands"], 1)

    def test_idempotent(self):
        once, _ = H.render(AGENT, self.home, "pl-", INDEX)
        twice, rep = H.render(once, self.home, "pl-", INDEX)
        self.assertEqual(once.replace("name: pl-a", "name: a"), twice.replace("name: pl-pl-a", "name: a"))
        self.assertEqual(rep["commands"], 0)

    def test_unknown_name_fails(self):
        with self.assertRaises(H.IndexError_):
            H.render("run cs-nope\n", self.home, "pl-", INDEX)

    def test_missing_wrapper_fails(self):
        os.remove(self.wrapper)
        with self.assertRaises(H.IndexError_):
            H.render(AGENT, self.home, "pl-", INDEX)


class TestRenderRealAgents(unittest.TestCase):
    """Every real agent renders for the host with the repo's .claude as home (no doubled paths, no container forms)."""

    def setUp(self):
        self.home = os.path.join(ROOT, ".claude")
        self.index = H.cmdindex.load(self.home)
        self.projects = H.cmdindex.root(self.index, "projects_dir", "host", self.home)
        src = os.path.join(self.home, "agents")
        self.rendered = {}
        for fn in sorted(os.listdir(src)):
            if fn.endswith(".md"):
                with open(os.path.join(src, fn), encoding="utf-8") as f:
                    self.rendered[fn] = H.render(f.read(), self.home, "pl-", self.index)[0]
        self.assertTrue(self.rendered)

    def test_no_doubled_home_prefix(self):
        for fn, text in self.rendered.items():
            self.assertNotIn(self.home + self.home, text, fn)
            for line in text.split("\n"):
                for tok in re.findall(r"/home/\S+", line):
                    segs = tok.split("/")
                    self.assertLessEqual(segs.count("home"), 1, f"{fn}: {tok}")

    def test_no_container_forms_left(self):
        for fn, text in self.rendered.items():
            self.assertNotIn("/root/.claude", text, fn)
            self.assertIsNone(re.search(r"/workspace", text.replace(self.projects, "")), fn)
            self.assertIsNone(re.search(r"(?<![\w/.-])cs-[A-Za-z]", text), fn)
            # render drops every lp instruction line; inline `lp` spans in prose are rewritten (INLINE_LP, LP_SPAN)
            self.assertFalse([ln for ln in text.split("\n") if ln != H.NAV_LINE and H.LP_LINE.match(ln)], fn)

    def test_only_deliberate_lp_note_left(self):
        note = "(`lp` is a container tool and is not installed on this host.)"
        for fn, text in self.rendered.items():
            self.assertIsNone(re.search(r"\blp\b", text.replace(note, "")), fn)

    def test_home_wrappers_exist(self):
        pat = re.compile(re.escape(self.home) + r"/bin/cs-[\w-]+")
        for fn, text in self.rendered.items():
            for path in set(pat.findall(text)):
                self.assertTrue(os.path.isfile(path), f"{fn}: {path}")


class TestInlineLp(unittest.TestCase):
    def test_pl_dev_sentence_rewritten(self):
        src = "for a bug fix list every caller of the function you touch (`lp nav refs FILE SYMBOL`, exact; grep only to confirm literal text) and fix"
        out, _, _, _ = H.render_body(src, "/h")
        self.assertEqual(out, "for a bug fix list every caller of the function you touch (Grep for the symbol; read the matches) and fix")

    def test_catch_all_span(self):
        out, _, _, _ = H.render_body('Use `lp find "question"` before grep.', "/h")
        self.assertEqual(out, "Use Grep/Glob before grep.")

    def test_description_reworded(self):
        out, _, _, _ = H.render_body("a script made it with `lp`; read it.", "/h")
        self.assertEqual(out, "a script made it; read it.")


if __name__ == "__main__":
    unittest.main()
