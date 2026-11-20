"""Import direction: no import cycle among the pipeline package's modules, and none between pipeline and the top-level scripts."""

import ast
import os
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(TOOLS, "pipeline")


def _edges():
    """-> {module: set(module)} over pipeline.* and the top-level scripts, function-level imports included."""
    tops = {f[:-3] for f in os.listdir(TOOLS) if f.endswith(".py")}
    mods = {f"pipeline.{f[:-3]}" for f in os.listdir(PKG) if f.endswith(".py") and f != "__init__.py"}
    graph = {}
    files = [(m, os.path.join(PKG, m.split(".")[1] + ".py")) for m in mods] + [(t, os.path.join(TOOLS, t + ".py")) for t in tops]
    for name, path in files:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        out = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level == 1 and name.startswith("pipeline."):
                for al in n.names:
                    out.add(f"pipeline.{n.module}" if n.module else f"pipeline.{al.name}")
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                if n.module == "pipeline":
                    out.update(f"pipeline.{al.name}" for al in n.names)
                else:
                    out.add(n.module)
            elif isinstance(n, ast.Import):
                out.update(al.name for al in n.names)
        graph[name] = {o for o in out if o in mods or o in tops}
    return graph


def _cycle(graph):
    state = {}

    def visit(n, path):
        state[n] = 1
        for m in sorted(graph.get(n, ())):
            if state.get(m) == 1:
                return [*path[path.index(m) :], n, m] if m in path else [n, m]
            if m not in state and (c := visit(m, [*path, n])):
                return c
        state[n] = 2
        return None

    for n in sorted(graph):
        if n not in state and (c := visit(n, [])):
            return c
    return None


class ImportCycleTest(unittest.TestCase):
    def test_no_cycles(self):
        self.assertIsNone(_cycle(_edges()))

    def test_cycle_finder_sees_a_cycle(self):
        self.assertIsNotNone(_cycle({"a": {"b"}, "b": {"a"}}))


# lazy, try-guarded entry points that need the model part (goal and phase digests); everything else must work with Strata down
# steps.py still imports with_docs/with_tests from plan_context (R10 owns steps.py: switch it to search.leads_docs, then drop it here)
MODEL_ENTRY = {"plan_run.py", "cli_feature.py"}


def _imports_plan_context(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (
            n.module == "localmodel.plan_context" or (n.module == "localmodel" and any(a.name == "plan_context" for a in n.names))
        ):
            return True
        if isinstance(n, ast.Import) and any(a.name == "localmodel.plan_context" for a in n.names):
            return True
    return False


class CoreNeedsNoModelTest(unittest.TestCase):
    def test_pipeline_and_audit_do_not_import_plan_context(self):
        bad = []
        for sub in ("pipeline", "audit"):
            d = os.path.join(TOOLS, sub)
            bad += [f"{sub}/{f}" for f in sorted(os.listdir(d)) if f.endswith(".py") and f not in MODEL_ENTRY and _imports_plan_context(os.path.join(d, f))]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
