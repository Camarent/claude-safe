import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARDED = ["model_guard.py", os.path.join("pipeline", "steps.py"), os.path.join("pipeline", "worktrees.py")]

# Model constants and their spelled-out names: a branch that picks one of these is a model route.
MODEL_NAMES = {"MODEL_SONNET", "MODEL_OPUS"}
MODEL_LITERALS = {"sonnet", "opus"}
# Calls that match a path, a file pattern or a glob: a route on these picks a model by file or topic.
ROUTING_CALLS = {"fnmatch", "fnmatchcase", "glob", "match", "fullmatch", "search", "endswith", "startswith"}
# Words that name a topic or a file area: a model chosen on them is a route on content.
ROUTING_WORDS = ("network", "parser", "docs", "migration", "security", "frontend", "backend", "auth")
WORD_RE = re.compile(r"\b(" + "|".join(ROUTING_WORDS) + r")\b", re.IGNORECASE)


def _is_routing_test(test):
    for node in ast.walk(test):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in ROUTING_CALLS:
                return True
        if isinstance(node, ast.Attribute) and node.attr == "suffix":
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and WORD_RE.search(node.value):
            return True
    return False


def _picks_model(branch_nodes):
    for root in branch_nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Name) and node.id in MODEL_NAMES:
                return True
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() in MODEL_LITERALS:
                return True
    return False


def find_routing(source, name="<src>"):
    """Return 'file:line' for each if/ternary whose test routes on a path, glob, suffix or topic word and whose branch picks a model."""
    findings = []
    for node in ast.walk(ast.parse(source, name)):
        branches: list[ast.AST]
        if isinstance(node, ast.If):
            branches = [*node.body, *node.orelse]
        elif isinstance(node, ast.IfExp):
            branches = [node.body, node.orelse]
        else:
            continue
        if _is_routing_test(node.test) and _picks_model(branches):
            findings.append(f"{name}:{node.lineno}")
    return findings


class EscalationNoRoutingTest(unittest.TestCase):
    def test_guarded_modules_route_no_model_by_path_or_topic(self):
        for rel in GUARDED:
            path = os.path.join(TOOLS, rel)
            with self.subTest(module=rel):
                with open(path, encoding="utf-8") as fh:
                    source = fh.read()
                self.assertEqual(find_routing(source, rel), [])

    def test_fnmatch_route_to_model_is_found(self):
        src = 'import fnmatch\nif fnmatch.fnmatch(path, "*.py"):\n    model = MODEL_OPUS\n'
        self.assertEqual(find_routing(src, "mut.py"), ["mut.py:2"])

    def test_glob_and_suffix_routes_are_found(self):
        src = "if glob.glob(pat):\n    m = MODEL_SONNET\nx = 'opus' if p.suffix == '.md' else 'haiku'\n"
        self.assertEqual(find_routing(src, "mut.py"), ["mut.py:1", "mut.py:3"])

    def test_topic_word_route_is_found(self):
        src = 'if "network" in topic:\n    model = MODEL_OPUS\n'
        self.assertEqual(find_routing(src, "mut.py"), ["mut.py:1"])

    def test_count_based_choice_is_not_a_route(self):
        src = "model = MODEL_OPUS if sonnet_devs >= 2 else MODEL_HAIKU\n"
        self.assertEqual(find_routing(src, "ok.py"), [])


if __name__ == "__main__":
    unittest.main()
