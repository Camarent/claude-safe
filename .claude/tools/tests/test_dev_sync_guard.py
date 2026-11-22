"""scripts/dev.sh sync refuses a symlinked .uv (a worktree's shared venv): run on a temp copy of the script, with a fake uv on PATH."""

import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEV_SH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "scripts", "dev.sh")
GUARD_MSG = "shared venv: sync in the main checkout"
FAKE_UV = '#!/bin/sh\necho ran >> "$FAKE_UV_LOG"\nexit 0\n'


class DevSyncGuardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # the temp root copy of dev.sh, its .uv and the fake uv: all removed at the end
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "root")
        self.bin = os.path.join(tmp.name, "bin")
        self.uv_log = os.path.join(tmp.name, "uv.log")
        os.makedirs(os.path.join(self.root, "scripts"))
        os.makedirs(self.bin)
        shutil.copy(DEV_SH, os.path.join(self.root, "scripts", "dev.sh"))
        with open(os.path.join(self.bin, "uv"), "w", encoding="utf-8") as f:
            f.write(FAKE_UV)
        os.chmod(os.path.join(self.bin, "uv"), 0o755)
        self.env = {**os.environ, "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""), "FAKE_UV_LOG": self.uv_log}

    def sync(self):
        return subprocess.run(
            ["bash", os.path.join(self.root, "scripts", "dev.sh"), "sync"],
            capture_output=True,
            text=True,
            check=False,
            env=self.env,
        )

    def test_symlinked_uv_refuses_sync(self):
        main_venv = os.path.join(os.path.dirname(self.root), "main-uv")
        os.makedirs(main_venv)
        os.symlink(main_venv, os.path.join(self.root, ".uv"))
        p = self.sync()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(GUARD_MSG, p.stderr)
        self.assertFalse(os.path.exists(self.uv_log), "uv must not run when the guard fires")

    def test_real_uv_dir_syncs(self):
        os.makedirs(os.path.join(self.root, ".uv"))
        p = self.sync()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(GUARD_MSG, p.stderr)
        self.assertTrue(os.path.exists(self.uv_log))


if __name__ == "__main__":
    unittest.main()
