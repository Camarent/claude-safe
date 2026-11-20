"""Run context of one wave-driver stage: paths, counters, the sessions of the current wave."""

import os

from .configuration import EV_DISPATCH, METRICS_LOG
from .limits import heavy_count
from .run import Run
from .util import paths, read_jsonl


class Ctx:
    def __init__(self, a):
        self.a = a = Run.of(a)  # the one frozen run state, built once from the CLI args
        self.stage, self.dir = a.stage, a.dir
        self.w, self.log = paths(a)
        self.metrics = os.path.join(self.w, METRICS_LOG)
        self.wave = self.mwave = self.limit = 0  # wave: label for metrics; mwave, limit: the limits (Sonnet/Opus sessions only)
        # disp, sessions: restored from orch.jsonl, so a restart (exit 3, crash, usage limit) does not grant a fresh allowance
        evs = read_jsonl(self.log)
        self.disp = heavy_count(evs)  # Sonnet/Opus dispatches so far (--max-dispatches)
        self.sessions = sum(1 for e in evs if e.get("ev") == EV_DISPATCH and e.get("agent") != "script")  # loop guard (all model sessions)
        self.cost = sum(
            r.get("cost_usd", 0.0) for r in read_jsonl(self.metrics) if r.get("ev") == "session"
        )  # budget covers the whole stage, also after a restart
        self.sess = []  # session lines of the current wave

    def tasks(self):
        """-> {dispatch id: task id} from orch.jsonl."""
        return {e["id"]: e.get("task", "") for e in read_jsonl(self.log) if e.get("ev") == "dispatch"}
