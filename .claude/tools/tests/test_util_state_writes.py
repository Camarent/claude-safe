"""util.write_text and append_jsonl: unique temp files, durable rename, one write per record; read_jsonl keeps objects only."""

import json
import os
import stat
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import util
from tests.support.tmpdirs import tracked_mkdtemp


class WriteText(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.f = os.path.join(self.d, "sub", "TASKS.md")

    def test_writes_creates_dirs_leaves_no_temp_and_keeps_an_existing_mode(self):
        util.write_text(self.f, "one")
        self.assertEqual(open(self.f).read(), "one")
        self.assertEqual(stat.S_IMODE(os.stat(self.f).st_mode), util.NEW_FILE_MODE)
        os.chmod(self.f, 0o600)
        util.write_text(self.f, "two")
        self.assertEqual(stat.S_IMODE(os.stat(self.f).st_mode), 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.f)), ["TASKS.md"])

    def test_threads_writing_one_path_do_not_collide(self):
        errors = []

        def work(n):
            try:
                for i in range(30):
                    util.write_text(self.f, f"{n}-{i}")
            except OSError as e:
                errors.append(e)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertRegex(open(self.f).read(), r"^\d-\d+$")
        self.assertEqual(os.listdir(os.path.dirname(self.f)), ["TASKS.md"])

    def test_a_failed_write_keeps_the_old_file_and_removes_the_temp(self):
        util.write_text(self.f, "old")
        with mock.patch.object(util.os, "replace", side_effect=OSError("disk")), self.assertRaises(OSError):
            util.write_text(self.f, "new")
        self.assertEqual(open(self.f).read(), "old")
        self.assertEqual(os.listdir(os.path.dirname(self.f)), ["TASKS.md"])


class Jsonl(unittest.TestCase):
    def test_append_is_one_write_and_repairs_a_torn_line(self):
        log = os.path.join(tracked_mkdtemp(), "orch.jsonl")
        util.append_jsonl(log, {"ev": "a"})
        with open(log, "a") as f:
            f.write('{"ev": "torn"')  # a crash left no newline
        util.append_jsonl(log, {"ev": "b"})
        lines = open(log).read().splitlines()
        self.assertEqual(json.loads(lines[0]), {"ev": "a"})
        self.assertEqual(json.loads(lines[-1]), {"ev": "b"})
        self.assertEqual(len(lines), 3)

    def test_read_jsonl_skips_non_object_lines_by_default(self):
        log = os.path.join(tracked_mkdtemp(), "x.jsonl")
        with open(log, "w") as f:
            f.write('{"ev": "a"}\n42\n[1]\n"s"\n{"ev": "b"}\n')
        self.assertEqual([r["ev"] for r in util.read_jsonl(log)], ["a", "b"])
        self.assertEqual(len(util.read_jsonl(log, dicts_only=False)), 5)


if __name__ == "__main__":
    unittest.main()
