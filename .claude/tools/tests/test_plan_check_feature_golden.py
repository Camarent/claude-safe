"""Characterization: exact problem lists of plan_check.check_feature / check_test_refs for synthetic plans.

Golden file: plan_check_feature_golden.json (re-record with `python3 tests/test_plan_check_feature_golden.py --record`)."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.plan_files import PHASE_HEAD

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_FILE = os.path.join(HERE, "plan_check_feature_golden.json")
CHECKLIST = "## Acceptance checklist\n- cmd: true\n"


def plan(rows, checklist=CHECKLIST):
    return f"# Plan: f\n\n## Goal\ng\n\n{checklist}\n## Phases\n{PHASE_HEAD}" + "".join(rows)


def row(name, goal="g", done="true", deps="", paths="src/a", tasks=20):
    return f"| {name} | {goal} | {done} | {deps} | {paths} | todo | {tasks} |\n"


# name -> (Plan.md text or None, pipeline.json dict or None, phase dirs to create, extra files {rel: text})
FEATURE_CASES = {
    "plan_missing": (None, None, [], {}),
    "checklist_empty": (plan([row("01-a"), row("02-b", paths="src/b")], checklist="## Acceptance checklist\n"), None, ["01-a", "02-b"], {}),
    "no_done_when": (plan([row("01-a", done=""), row("02-b", paths="src/b")]), None, ["01-a", "02-b"], {}),
    "done_when_sentence": (plan([row("01-a", done="all tests pass; ok (really)"), row("02-b", paths="src/b")]), None, ["01-a", "02-b"], {}),
    "done_when_cd": (plan([row("01-a", done="cd proj && true"), row("02-b", paths="src/b")]), {"repo": "proj"}, ["01-a", "02-b"], {}),
    "folder_missing": (plan([row("01-a"), row("02-b", paths="src/b")]), None, ["01-a"], {}),
    "shared_paths": (plan([row("01-a"), row("02-b", paths="src/a/x")]), None, ["01-a", "02-b"], {}),
    "bad_depends": (
        plan([row("01-a", deps="zzz"), row("02-b", paths="src/b", deps="late"), row("03-late", paths="src/c")]),
        None,
        ["01-a", "02-b", "03-late"],
        {},
    ),
    "good_depends": (plan([row("01-a"), row("02-b", paths="src/b", deps="a")]), None, ["01-a", "02-b"], {}),
    "bump_phase": (plan([row("01-a", goal="Bump to v0.3.0"), row("02-b", paths="src/b")]), {"release_script": "x.sh"}, ["01-a", "02-b"], {}),
    "stage_problems": (
        plan([row("01-a"), row("02-b", paths="src/b")]),
        None,
        ["01-a", "02-b"],
        {"phases/01-a/TASKS.md": "## Tasks\n- [ ] task-1 one - owner: haiku - review: wave W1, haiku - depends: task-9\n"},
    ),
}

REFS_CASES = {
    "no_symbols": ("Goal: plain words\nPredicted paths: web/a.js\n", {"web/tests/c.test.js": "clipMeta"}),
    "no_predicted": ("Goal: `clipMeta` x\n", {"web/tests/c.test.js": "clipMeta"}),
    "hit": ("Goal: `clipMeta` x\nPredicted paths: web/a.js\nDone: `otherSym` ok\n", {"web/tests/c.test.js": "clipMeta(1)", "web/spec_x.js": "otherSym"}),
    "predicted_test": ("Goal: `clipMeta` x\nPredicted paths: web/tests/\n", {"web/tests/c.test.js": "clipMeta"}),
    "named_in_text": ("Goal: `clipMeta` x\nPredicted paths: web/a.js\nweb/tests/c.test.js stays\n", {"web/tests/c.test.js": "clipMeta"}),
    "no_match": ("Goal: `clipMeta` x\nPredicted paths: web/a.js\n", {"web/tests/c.test.js": "nothing", "web/src/main.js": "clipMeta"}),
    "cap_five": ("Goal: `clipMeta` x\nPredicted paths: web/a.js\n", {f"t/test_{i}.py": "clipMeta" for i in range(7)}),
}


def put(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run_feature(case):
    md, cfg, dirs, extra = FEATURE_CASES[case]
    root = tempfile.mkdtemp(prefix="synthetic-feature-")
    try:
        fdir = os.path.join(root, ".work", "f")
        os.makedirs(fdir)
        if md is not None:
            put(fdir, "Plan.md", md)
        if cfg:
            put(root, "pipeline.json", json.dumps(cfg))
        for d in dirs:
            os.makedirs(os.path.join(fdir, "phases", d))
        for rel, text in extra.items():
            put(fdir, rel, text)
        n, probs = plan_check.check_feature(argparse.Namespace(dir=root, feature="f"), fdir)
        return [n] + [p.replace(root, "ROOT") for p in probs]
    finally:
        shutil.rmtree(root, True)


def run_refs(case):
    text, files = REFS_CASES[case]
    root = tempfile.mkdtemp(prefix="synthetic-refs-")
    try:
        put(root, "task-1.md", text)
        for rel, body in files.items():
            put(root, rel, body)
        return plan_check.check_test_refs(root, "task-1", root)
    finally:
        shutil.rmtree(root, True)


class FeatureGoldenTest(unittest.TestCase):
    golden: dict

    @classmethod
    def setUpClass(cls):
        with open(GOLDEN_FILE, encoding="utf-8") as f:
            cls.golden = json.load(f)

    def test_feature_cases(self):
        for case in FEATURE_CASES:
            with self.subTest(case):
                self.assertEqual(run_feature(case), self.golden["feature"][case])

    def test_refs_cases(self):
        for case in REFS_CASES:
            with self.subTest(case):
                got, want = run_refs(case), self.golden["refs"][case]
                if case == "cap_five":  # os.walk order is filesystem-dependent: pin the cap and the message shape only
                    self.assertEqual(len(got), 5)
                    self.assertTrue(
                        all(m.startswith("task-1: test t/test_") and m.endswith("is not in Predicted paths (add it, or say why it stays)") for m in got)
                    )
                else:
                    self.assertEqual(sorted(got), sorted(want))


if __name__ == "__main__":
    if sys.argv[1:] == ["--record"]:
        out = {"feature": {c: run_feature(c) for c in FEATURE_CASES}, "refs": {c: run_refs(c) for c in REFS_CASES}}
        with open(GOLDEN_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True)
    else:
        unittest.main()
