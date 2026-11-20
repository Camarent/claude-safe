import os
import sys
import threading
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import localq
from tests.support.tmpdirs import tracked_mkdtemp


class T(unittest.TestCase):
    def setUp(self):  # own lock dir and limit per test: parallel workers must not share /tmp/localq
        for p in (mock.patch.object(localq, "DIR", tracked_mkdtemp()), mock.patch.dict(os.environ, {"LOCALQ_CALLS": "2"})):
            p.start()
            self.addCleanup(p.stop)

    def test_default_is_one_call_slot(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("LOCALQ_CALLS", None)
            self.assertEqual(localq.limits()["call"], 1)

    def test_at_most_n_run_at_once_and_all_finish(self):
        running, peak, done = [0], [0], []
        lock = threading.Lock()
        two_inside = threading.Event()

        def work(i):
            with localq.slot("call"):
                with lock:
                    running[0] += 1
                    peak[0] = max(peak[0], running[0])
                    if running[0] == 2:
                        two_inside.set()
                two_inside.wait(10)  # hold the slot until a second thread is inside: no timing guess
                with lock:
                    running[0] -= 1
                    done.append(i)

        ts = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(done), 6)
        self.assertLessEqual(peak[0], 2)
        self.assertGreaterEqual(peak[0], 2)

    def test_slot_released_after_exception(self):
        try:
            with localq.slot("call"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        entered = []
        with localq.slot("call"), localq.slot("call"):
            entered.append(2)  # both slots are free again
        self.assertEqual(entered, [2])

    def test_timeout_when_all_busy(self):
        with localq.slot("call"), localq.slot("call"), self.assertRaises(TimeoutError), localq.slot("call", timeout=0.5):
            pass

    def test_unopenable_lock_file_does_not_crash(self):  # a root-owned lock left by the container: no lock, no crash
        os.makedirs(os.path.join(localq.DIR, "call0.lock"))  # a directory at the lock path: open() fails like a foreign-owned file
        with localq.slot("call"):
            pass

    def test_status_lists_slots(self):
        with localq.slot("call"):
            self.assertIn("busy", localq.status())


if __name__ == "__main__":
    unittest.main()
