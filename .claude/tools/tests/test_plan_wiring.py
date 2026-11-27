"""plan_wiring.check_wiring: a new module under a wiring dir needs a mount (`wiring:`); a `Mounts:` header needs an entry-proof Done (`reach:`). Temp git repo, no wiring_dirs setting = no check."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx, plan_check, plan_problem, plan_wiring

ROUTER = "task-1"  # adds extension/src/ui/router.ts under the wiring dir
MOUNT = "task-2"  # depends on ROUTER and mounts it
WIRING = "wiring:"
REACH = "reach:"
UI = "extension/src/ui"


class WiringTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)
        self.setting({"wiring_dirs": [UI]})
        gitx.run(self.root, "init", "-q")
        self.track("extension/src/main.ts")

    def setting(self, cfg):
        with open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def track(self, path):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write("\n")
        gitx.run(self.root, "add", path)

    def task(self, name, body):
        with open(os.path.join(self.d, name + ".md"), "w", encoding="utf-8") as f:
            f.write(body)

    def ents(self, mount_depends=(ROUTER,)):
        return [{"id": ROUTER, "depends": []}, {"id": MOUNT, "depends": list(mount_depends)}]

    def router(self, extra=""):
        self.task(ROUTER, f"# T1\nPredicted paths: {UI}/router.ts{extra}\nDone: C1: npm test passes\n")

    def mount(self, mounts=f"Mounts: {ROUTER}\n", done="Done: C1: e2e from built bundle\n"):
        self.task(MOUNT, f"# T2\n{mounts}Predicted paths: extension/src/main.ts\n{done}")

    def test_no_setting_no_check(self):
        self.setting({})
        self.router()
        self.assertEqual(plan_wiring.check_wiring(self.d, self.ents()), [])

    def test_new_module_without_mount_errors(self):
        self.router()
        self.task(MOUNT, "# T2\nPredicted paths: extension/src/other.ts\nDone: C1: npm test passes\n")
        probs = plan_wiring.check_wiring(self.d, self.ents())
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith(f"{WIRING} {ROUTER}: {UI}/router.ts"))

    def test_mount_point_in_same_task_passes(self):
        self.router(extra=", extension/src/main.ts")
        self.task(MOUNT, "# T2\nPredicted paths: extension/src/other.ts\nDone: C1: npm test passes\n")
        self.assertEqual(plan_wiring.check_wiring(self.d, self.ents()), [])

    def test_dependent_mounts_header_with_entry_proof_passes(self):
        self.router()
        self.mount()
        self.assertEqual(plan_wiring.check_wiring(self.d, self.ents()), [])

    def test_mounts_header_naming_other_task_still_errors(self):
        self.router()
        self.mount(mounts="Mounts: task-9\n")
        probs = plan_wiring.check_wiring(self.d, self.ents())
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith(WIRING))

    def test_mounts_header_without_entry_proof_warns(self):
        self.router()
        self.mount(done="Done: C1: npm test passes with a mock harness\n")
        probs = plan_wiring.check_wiring(self.d, self.ents())
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith(f"{REACH} {MOUNT}:"))
        self.assertEqual(plan_problem.Problem.parse(probs[0]).level, plan_problem.WARN)

    def test_tracked_module_in_wiring_dir_passes(self):
        self.track(f"{UI}/router.ts")
        self.task(ROUTER, f"# T1\nPredicted paths: {UI}/router.ts\nDone: C1: npm test passes\n")
        self.assertEqual(plan_wiring.check_wiring(self.d, self.ents()), [])

    def test_wiring_error_is_error_level(self):
        self.router()
        self.task(MOUNT, "# T2\nPredicted paths: extension/src/other.ts\nDone: C1: npm test passes\n")
        probs = plan_wiring.check_wiring(self.d, self.ents())
        self.assertEqual(plan_problem.Problem.parse(probs[0]).level, plan_problem.ERROR)

    def test_plan_check_wires_the_rule(self):
        self.assertIs(plan_check.check_wiring, plan_wiring.check_wiring)
