"""Shared entry-point helpers: the status-line formatter, the run flags, the stage name and the open-phase resolver. Stdlib only; imports no cli_* module."""

import os
from pathlib import Path

from . import plan_files as pf
from .configuration import BLOCKED_FILE
from .run import Run

PHASES_SEP = "/phases/"  # a phase stage is `<feature>/phases/<NN-name>`


def _feature_dir_of_phase(stage_dir):
    """-> the feature folder of a phase stage dir `F/phases/NN`, None for a single-stage dir."""
    parent = os.path.dirname(os.path.normpath(stage_dir))
    if os.path.basename(parent) != PHASES_SEP.strip("/"):
        return None
    return os.path.dirname(parent)


def write_blocked(stage_dir, msg) -> None:
    """Write the BLOCKED report `msg` to stage_dir/BLOCKED.md. For a phase dir `F/phases/NN` also write F/BLOCKED.md, first line naming the phase folder; the phase copy stays."""
    os.makedirs(stage_dir, exist_ok=True)
    with open(os.path.join(stage_dir, BLOCKED_FILE), "w", encoding="utf-8") as fh:
        fh.write(msg)
    feature = _feature_dir_of_phase(stage_dir)
    if feature is None:
        return
    phase = os.path.basename(os.path.normpath(stage_dir))
    with open(os.path.join(feature, BLOCKED_FILE), "w", encoding="utf-8") as fh:
        fh.write(f"# Blocked in phase {phase}: see {phase}/{BLOCKED_FILE}\n\n" + msg)


def clear_blocked(stage_dir) -> None:
    """Remove the BLOCKED report of stage_dir and, for a phase dir, the feature folder copy. Missing files are fine."""
    feature = _feature_dir_of_phase(stage_dir)
    Path(stage_dir, BLOCKED_FILE).unlink(missing_ok=True)
    if feature is not None:
        Path(feature, BLOCKED_FILE).unlink(missing_ok=True)


def blocked_line(where, msg) -> str:
    """The BLOCKED text a run prints when it stops (the file it also wrote lives in WHERE)."""
    return f"BLOCKED, back to lead (also in {where}/BLOCKED.md):\n{msg}"


def limit_line(text) -> str:
    """The text a run prints when a limit stops it, e.g. limit_line('max-waves 3')."""
    return f"limit: {text}"


def banner(text) -> str:
    """A section line of a run: `== text`."""
    return f"== {text}"


def done_line(msg) -> str:
    """The last line of a finished run: `done: msg`."""
    return f"done: {msg}"


def alias_line(old, new) -> str:
    """The note an old form prints before it runs the new form: `note: OLD is an alias of NEW`."""
    return f"note: `{old}` is an alias of `{new}`"


def name_of_stage(stage) -> str:
    """The feature name of a stage: `F` for `F/phases/NN`, the name unchanged otherwise."""
    return stage.split(PHASES_SEP, 1)[0]


def add_run_flags(p) -> None:
    """The limit, dry-run and stub flags of a run command (argparse parser P)."""
    p.add_argument("--max-waves", type=int, default=None)
    p.add_argument("--max-dispatches", type=int, default=None)
    p.add_argument("--max-sessions", type=int, default=None)
    p.add_argument("--max-budget-usd", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--stub",
        action="append",
        default=[],
        metavar="TASK:ROLE=V1[,V2]",
        help="dry-run only: force the verdicts of one task's role, e.g. task-1:dev=BLOCKED or task-1:dev=ESCALATE,DONE",
    )
    p.add_argument("--allow-dirty", action="store_true")


def resolve_open_phase(d, name) -> str:
    """-> the stage to run next. A feature with phases in its Plan.md gives `NAME/phases/NN` of the first open phase; a name that already holds `/phases/` or has no phases comes back unchanged. All phases done raises ValueError naming NAME."""
    if PHASES_SEP in name:
        return name
    phases = pf.read_phases(Run(dir=d, feature=name))
    if not phases:
        return name
    phase = pf.next_open_phase(phases)
    if phase is None:
        raise ValueError(f"no open phase left in {name}: all phases are done")
    return pf.stage_of(name, phase)
