import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ui_inventory_check as uic


def screen(sid, goto=(), exits=(), title="Home", labels=None, terms=(), data_source="api"):
    """One screen dict: controls go to GOTO ids, EXITS are exit ids; LABELS default to one label per goto."""
    labels = labels if labels is not None else [f"go {g}" for g in goto]
    return {
        "id": sid,
        "title": title,
        "data_source": data_source,
        "controls": [{"label": lb, "goto": g} for lb, g in zip(labels, goto, strict=True)],
        "exits": list(exits),
        "terms": list(terms),
    }


GLOSSARY = ["home", "go", "open", "list", "api", "detail"]


def clean_inventory():
    return {
        "glossary": GLOSSARY,
        "entry": "home",
        "screens": [
            screen("home", goto=["list"], title="Home", labels=["open list"]),
            screen("list", goto=["detail"], title="List", labels=["open detail"], terms=["api"]),
            screen("detail", goto=["home"], title="Detail", labels=["go home"]),
        ],
    }


class UiInventoryCheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def run_check(self, inv):
        """Write INV to a JSON file, run the checker's main on it; -> (exit code, printed lines)."""
        path = os.path.join(self.dir, "inventory.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(inv, fh)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = uic.main([path])
        return code, buf.getvalue().splitlines()

    def test_clean_inventory_exits_zero(self):
        code, lines = self.run_check(clean_inventory())
        self.assertEqual(code, 0)
        self.assertEqual(lines, [])

    def test_unreachable_screen_is_flagged(self):
        inv = clean_inventory()
        inv["screens"].append(screen("orphan", goto=["home"], title="Home", labels=["go home"]))
        code, lines = self.run_check(inv)
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["unreachable: orphan"])

    def test_dead_control_link_is_flagged(self):
        inv = clean_inventory()
        inv["screens"][0]["controls"][0]["goto"] = "nowhere"
        code, lines = self.run_check(inv)
        self.assertEqual(code, 1)
        self.assertIn("dead-link: home -> 'nowhere'", lines)

    def test_dead_exit_is_flagged(self):
        inv = clean_inventory()
        inv["screens"][2]["exits"] = ["ghost"]
        code, lines = self.run_check(inv)
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["dead-link: detail -> 'ghost'"])

    def test_term_outside_glossary_is_flagged(self):
        inv = clean_inventory()
        inv["screens"][1]["terms"] = ["api", "widget"]
        code, lines = self.run_check(inv)
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["term: list 'widget' not in glossary"])

    def test_title_word_outside_glossary_is_flagged_case_insensitive(self):
        inv = clean_inventory()
        inv["screens"][2]["title"] = "Detail Gadget"
        code, lines = self.run_check(inv)
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["term: detail 'gadget' not in glossary"])

    def test_non_ascii_title_words_are_checked_whole(self):
        inv = clean_inventory()
        inv["glossary"] = [*GLOSSARY, "café", "список"]
        inv["screens"][2]["title"] = "Café Список"
        code, lines = self.run_check(inv)
        self.assertEqual(code, 0)
        self.assertEqual(lines, [])

    def test_non_ascii_word_outside_glossary_is_flagged_whole(self):
        inv = clean_inventory()
        inv["glossary"] = [*GLOSSARY, "мой"]
        inv["screens"][2]["title"] = "Café Мой"
        code, lines = self.run_check(inv)
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["term: detail 'café' not in glossary"])

    def test_glossary_match_ignores_case(self):
        inv = clean_inventory()
        inv["glossary"] = [g.upper() for g in GLOSSARY]
        inv["screens"][1]["terms"] = ["API"]
        code, _ = self.run_check(inv)
        self.assertEqual(code, 0)

    def test_screen_reachable_only_via_exit_is_not_flagged(self):
        inv = clean_inventory()
        inv["screens"][0]["controls"] = []
        inv["screens"][0]["exits"] = ["list"]
        inv["screens"][1]["controls"] = []
        inv["screens"][1]["exits"] = ["detail"]
        code, lines = self.run_check(inv)
        self.assertEqual(code, 0)
        self.assertEqual(lines, [])

    def test_missing_data_source_or_links_is_flagged(self):
        inv = clean_inventory()
        inv["screens"][2]["data_source"] = ""
        inv["screens"][1]["controls"] = []
        inv["screens"][1]["exits"] = []
        code, lines = self.run_check(inv)
        self.assertEqual(code, 1)
        self.assertIn("missing: detail needs a data_source and controls or exits", lines)
        self.assertIn("missing: list needs a data_source and controls or exits", lines)

    def test_invalid_shape_exits_one(self):
        code, lines = self.run_check({"glossary": [], "screens": "nope"})
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("invalid:"))

    def test_non_string_entry_exits_one_with_invalid_line(self):
        inv = clean_inventory()
        inv["entry"] = ["home"]
        code, lines = self.run_check(inv)
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("invalid: `entry`"))

    def test_unreadable_file_exits_one(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = uic.main([os.path.join(self.dir, "absent.json")])
        self.assertEqual(code, 1)
        self.assertTrue(buf.getvalue().startswith("invalid: cannot read"))


if __name__ == "__main__":
    unittest.main()
