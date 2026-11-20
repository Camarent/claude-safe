"""Which phases of a big plan get audited: the last one (the finish phase when there is one) always, a middle one once enough tasks piled up since the last audit; the last task phase before finish never."""

import glob
import os

from .configuration import TASKS_FILE
from .plan_files import is_finish_dir, read_phases
from .run import Run
from .tasks import parse_tasks

DEFAULT_EVERY = 25  # tasks without an audit before a middle phase is audited anyway (`audit_every_tasks` in pipeline.json)


def audit_due(stage_folder, n_tasks, every=DEFAULT_EVERY, dir="."):
    """True for an ordinary stage and for the last phase. A middle phase: True when its tasks plus the tasks of earlier phases since the last audited phase reach `every`."""
    folder = os.path.abspath(stage_folder)
    parent = os.path.dirname(folder)
    if os.path.basename(parent) != "phases":
        return True
    feature = os.path.dirname(parent)
    phases = read_phases(Run(dir=dir, feature=os.path.basename(feature)))
    names = [p["dir"] for p in phases]
    cur = os.path.basename(folder)
    if cur not in names or names[-1] == cur:
        return True
    if is_finish_dir(names[names.index(cur) + 1]):
        return False  # the last task phase: the finish phase audits it together with the earlier ones
    pending = 0
    for name in names[: names.index(cur)]:
        d = os.path.join(parent, name)
        if any(glob.glob(os.path.join(d, sub, pat)) for sub in ("audits", "") for pat in ("audit-sec*.md", "audit-arch*.md")):
            pending = 0  # that phase was audited: count from there
        else:
            pending += len(parse_tasks(os.path.join(d, TASKS_FILE)))
    return pending + n_tasks >= every
