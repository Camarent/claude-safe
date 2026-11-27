"""pipeline.gate_slots.held: N slots cap the gate runs that hold one at once; 0 = no cap; a slot is free again when its holder leaves the with-block. Runners are threads (each opens its own slot file, so flock treats them as separate holders). Polls go through an injected `Spinner` instead of time.sleep: it yields the CPU and reads no clock."""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gate_slots

WAIT_S = 10  # guard only: a barrier or event that never opens fails the test instead of hanging it
SPIN_LIMIT = 1_000_000  # guard only: a slot that never frees fails the waiter instead of spinning forever


class Spinner:
    """Injected as the `sleep` of gate_slots.held: records each poll, sets `blocked` on the first one, and yields the CPU."""

    def __init__(self):
        self.polls = 0
        self.blocked = threading.Event()  # set once some runner has polled, i.e. found every slot taken

    def __call__(self, seconds):
        del seconds  # the poll interval is not waited on: no clock here
        self.polls += 1
        self.blocked.set()
        if self.polls > SPIN_LIMIT:
            raise TimeoutError("gate slot never freed")
        os.sched_yield()


class Gauge:
    """Counts the runners inside their slot and keeps the peak."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def enter(self):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def leave(self):
        with self.lock:
            self.active -= 1


def run_all(runners):
    """Start every runner thread, join them all, re-raise the first error."""
    errors = []

    def wrap(fn):
        try:
            fn()
        except BaseException as e:  # a thread error must reach the test, not die silently
            errors.append(e)

    threads = [threading.Thread(target=wrap, args=(fn,)) for fn in runners]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT_S * 3)
    if errors:
        raise errors[0]


class TestHeld(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = tmp.name

    def test_one_slot_two_runners_never_overlap(self):
        gauge = Gauge()
        spin = Spinner()
        holding = threading.Event()  # set once the first runner is inside

        def first():
            with gate_slots.held(1, self.base, spin):
                gauge.enter()
                holding.set()
                spin.blocked.wait(WAIT_S)  # keep the slot until the second runner has found it taken
                gauge.leave()

        def second():
            holding.wait(WAIT_S)
            with gate_slots.held(1, self.base, spin):
                gauge.enter()
                gauge.leave()

        run_all([first, second])
        self.assertTrue(spin.blocked.is_set(), "second runner never found the slot taken")
        self.assertEqual(gauge.peak, 1)

    def test_nineteen_runners_three_slots_peak_is_three(self):
        gauge = Gauge()
        spin = Spinner()
        full = threading.Event()  # set once three runners are inside together
        all_started = threading.Barrier(19)  # every runner is about to ask for a slot

        def runner():
            all_started.wait(WAIT_S)
            with gate_slots.held(3, self.base, spin):
                gauge.enter()
                if gauge.active == 3:
                    full.set()
                full.wait(WAIT_S)  # the holders keep their slots until three are in together
                for _ in range(2000):  # yield so an uncapped fourth runner can enter while three hold
                    os.sched_yield()
                gauge.leave()

        run_all([runner] * 19)
        self.assertTrue(full.is_set(), "three runners never held slots at once")
        self.assertEqual(gauge.peak, 3)

    def test_waiter_wakes_when_a_non_zero_slot_frees(self):
        fds = [gate_slots._try_slot(os.path.join(self.base, f"slot-{i}")) for i in range(3)]
        self.assertNotIn(None, fds)
        self.addCleanup(lambda: [os.close(fd) for fd in fds if fd is not None])
        spin = Spinner()
        entered = threading.Event()

        def waiter():
            with gate_slots.held(3, self.base, spin):
                entered.set()

        w = threading.Thread(target=waiter)
        w.start()
        self.assertTrue(spin.blocked.wait(WAIT_S), "waiter never polled")
        freed = fds[1]
        if freed is None:
            self.fail("slot-1 was not held by the test")
        os.close(freed)  # only slot-1 frees; slot-0 and slot-2 stay held
        fds[1] = None
        self.assertTrue(entered.wait(WAIT_S), "waiter stayed blocked while slot-1 was free")
        w.join(WAIT_S)

    def test_zero_means_no_cap(self):
        with gate_slots.held(0, self.base), gate_slots.held(0, self.base):
            pass
        self.assertEqual(os.listdir(self.base), [])  # no cap: no slot file is made

    def test_slot_is_free_after_its_holder_leaves(self):
        spin = Spinner()
        with gate_slots.held(1, self.base, spin):
            pass
        entered = []

        def third():  # would poll until SPIN_LIMIT if the first holder kept the slot
            with gate_slots.held(1, self.base, spin):
                entered.append(True)

        run_all([third])
        self.assertEqual(entered, [True])

    def test_slot_files_live_in_the_given_folder(self):
        spin = Spinner()
        with gate_slots.held(2, self.base, spin), gate_slots.held(2, self.base, spin):  # the second takes the slot the first left
            self.assertEqual(sorted(os.listdir(self.base)), ["slot-0", "slot-1"])

    def test_slot_dir_is_per_user(self):
        self.assertEqual(os.path.basename(gate_slots.slot_dir()), str(os.getuid()))


if __name__ == "__main__":
    unittest.main()
