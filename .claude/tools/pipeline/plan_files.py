"""Plan.md / TASKS.md / task-N.md formats written by plan.py (parsed by tasks.parse_tasks and brieflint) and the folder rules."""

import os
import re

from .configuration import (
    ACCEPTANCE_CMD_TAG,
    ACCEPTANCE_EYES_TAG,
    ORCH_LOG,
    PHASES_DIR,
    PLAN_FILE,
    PLAN_MAX_PHASES,
    STARTED_EVENTS,
    TASKS_FILE,
    WORK_DIR,
    tool_cmd,
)
from .stagedir import resolve_stage
from .util import read_jsonl, read_text, write_text

QRUN = f"{tool_cmd('qrun.py')} -- "
CHECKLIST_HEAD = "## Acceptance checklist"  # `plan-init` writes it empty; `check` fails until it holds `- cmd:` / `- eyes:` lines


def section_body(text, is_head, stop_prefix):
    """The text after the first line `is_head` accepts (that line must end with a newline), up to the next line that starts with `stop_prefix`; None when there is no such heading."""
    lines = text.split("\n")
    for i, line in enumerate(lines[:-1]):
        if is_head(line):
            body = []
            for nxt in lines[i + 1 :]:
                if nxt.startswith(stop_prefix):
                    break
                body.append(nxt)
            return "\n".join(body)
    return None


def checklist_items(plan_md_text):
    """Bullet lines under `## Acceptance checklist` (up to the next `## `); [] when the section is missing or empty."""
    body = section_body(plan_md_text, lambda ln: ln.rstrip(" \t") == CHECKLIST_HEAD, "## ")
    return [ln for ln in (body or "").splitlines() if re.match(r"\s*[-*] \S", ln)]


_EXPECTED_RE = re.compile(r"\s+\([^()]*\)\s*$")  # trailing `(expected result)` of a cmd line


def acceptance_items(plan_md_text):
    """-> [(tag, text)] of the checklist lines, tag = `cmd:`, `eyes:` or "" for a line with neither. A cmd text loses surrounding backticks and a trailing `(expected result)`."""
    out = []
    for ln in checklist_items(plan_md_text):
        body = re.sub(r"^\s*[-*] ", "", ln).strip()
        tag = next((t for t in (ACCEPTANCE_CMD_TAG, ACCEPTANCE_EYES_TAG) if body.startswith(t)), "")
        text = body[len(tag) :].strip()
        if tag == ACCEPTANCE_CMD_TAG:
            text = (_EXPECTED_RE.sub("", text) or text).strip("` ")
        out.append((tag, text if tag else body))
    return out


PHASE_HEAD = "| phase | goal | done_when | depends | paths | status | tasks |\n|---|---|---|---|---|---|---|\n"


class PlanError(Exception):
    """Invalid plan input; the CLI prints it and exits 2."""


def stage_of(feature, phase):
    return f"{feature}/phases/{phase['dir']}"


def feature_dir(a):
    return os.path.join(a.dir, WORK_DIR, a.feature)


def stage_dir(a):
    """`.work/S`, else `.work/*/phases/S` (S may also be `F/phases/NN-name`)."""
    return resolve_stage(a.dir, a.stage, strict=False, phases=True)


def csv(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def cell(s):
    return str(s).replace("|", "/").replace("\n", " ").strip()


def overlap(p1, p2):
    """-> first (a, b) pair of paths of two lists that are equal or nested, else None."""
    for a in p1:
        for b in p2:
            x, y = a.rstrip("/"), b.rstrip("/")
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                return a, b
    return None


# ---- Plan.md -------------------------------------------------------------


def plan_path(a):
    return os.path.join(feature_dir(a), PLAN_FILE)


def init_plan(a):
    os.makedirs(os.path.join(feature_dir(a), "phases"), exist_ok=True)
    write_text(plan_path(a), f"# Plan: {a.feature}\n\n## Goal\n{a.goal.strip()}\n\n{CHECKLIST_HEAD}\n\n## Phases\n{PHASE_HEAD}")


def read_phases(a):
    """-> list of {dir, goal, done_when, depends[], paths[], status, tasks} from the Plan.md table (tasks = the architect's estimate, 0 = unknown)."""
    return parse_phase_rows(read_text(plan_path(a)))


def _is_phase_dir(name):
    """`NN-name`: two digits, a dash, then a non-empty single-line rest."""
    return len(name) > 3 and name[:2].isdecimal() and name[2] == "-" and "\n" not in name[3:]


def parse_phase_rows(plan_md_text):
    out = []
    for line in plan_md_text.splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) in (6, 7) and _is_phase_dir(c[0]):  # 6 columns = a Plan.md written before the tasks estimate
            est = int(c[6]) if len(c) == 7 and c[6].isdigit() else 0
            out.append({"dir": c[0], "goal": c[1], "done_when": c[2], "depends": csv(c[3]), "paths": csv(c[4]), "status": c[5], "tasks": est})
    return out


def is_mid_phase(tasks_md):
    """True when `tasks_md` sits in `.work/<feature>/phases/NN-name/` and that phase is not the last row of the feature's Plan.md. Decided from Plan.md at run time, so phases added after the stage was created still count."""
    folder = os.path.dirname(os.path.abspath(tasks_md))
    parent = os.path.dirname(folder)
    if os.path.basename(parent) != "phases":
        return False
    names = [p["dir"] for p in parse_phase_rows(read_text(os.path.join(os.path.dirname(parent), PLAN_FILE)))]
    return os.path.basename(folder) in names and names[-1] != os.path.basename(folder)


def is_last_phase(folder):
    """True when `folder` is `.work/<feature>/phases/NN-name/` and that phase is the last row of the feature's Plan.md (the finish phase when there is one)."""
    parent = os.path.dirname(os.path.abspath(folder))
    if os.path.basename(parent) != PHASES_DIR:
        return False
    names = [p["dir"] for p in parse_phase_rows(read_text(os.path.join(os.path.dirname(parent), PLAN_FILE)))]
    return bool(names) and names[-1] == os.path.basename(os.path.abspath(folder))


FINISH_RE = re.compile(r"\d\d-finish")
PATH_DIRS = r"(?:[\w.-]+/)"  # the path-token pieces the plan and request scanners share: directory run, file name
PATH_NAME = r"[\w.-]+"
PATH_FILE = rf"{PATH_DIRS}*{PATH_NAME}\.\w{{1,6}}"  # optional directories, a name, an extension of 1-6 word characters


def is_finish_dir(name):
    """True for the closing phase of a feature (`NN-finish`): no tasks of its own; it runs tests, audits, master merge, push, index, cleanup and the feature retro."""
    return bool(FINISH_RE.fullmatch(name))


def is_finish_phase(tasks_md):
    """True when `tasks_md` sits in a `.work/<feature>/phases/NN-finish/` folder."""
    folder = os.path.dirname(os.path.abspath(tasks_md))
    return os.path.basename(os.path.dirname(folder)) == "phases" and is_finish_dir(os.path.basename(folder))


def is_later_phase(tasks_md):
    """True when `tasks_md` sits in `.work/<feature>/phases/NN-name/` and a Plan.md row comes before that phase (phase 2 and later, the finish phase too)."""
    folder = os.path.dirname(os.path.abspath(tasks_md))
    parent = os.path.dirname(folder)
    if os.path.basename(parent) != PHASES_DIR:
        return False
    names = [p["dir"] for p in parse_phase_rows(read_text(os.path.join(os.path.dirname(parent), PLAN_FILE)))]
    return os.path.basename(folder) in names and names.index(os.path.basename(folder)) > 0


def earlier_phase_tasks_mds(finish_tasks_md):
    """TASKS.md paths of the phases before the finish phase, in Plan.md order (the audits look at their tasks)."""
    folder = os.path.dirname(os.path.abspath(finish_tasks_md))
    phases_dir = os.path.dirname(folder)
    names = [p["dir"] for p in parse_phase_rows(read_text(os.path.join(os.path.dirname(phases_dir), PLAN_FILE)))]
    out = [os.path.join(phases_dir, n, TASKS_FILE) for n in names if not is_finish_dir(n)]
    return [t for t in out if os.path.exists(t)]


def next_phase_no(phases):
    """Number of the phase after the last one. 99 is the last: `100-` breaks the NN- folder names."""
    n = int(phases[-1]["dir"][:2]) + 1
    if n > PLAN_MAX_PHASES:
        raise ValueError(f"Plan.md has phase {phases[-1]['dir']}: no NN- number left for the finish phase (phases end at 99)")
    return n


def ensure_finish(a):
    """Append the `NN-finish` row to Plan.md when the feature has task phases and none yet. -> True when it added the row."""
    phases = read_phases(a)
    if not phases or any(is_finish_dir(p["dir"]) for p in phases):
        return False
    n = next_phase_no(phases)
    phases.append(
        {
            "dir": f"{n:02d}-finish",
            "goal": "tests, audits, master merge, push, cleanup, feature retro",
            "done_when": "true",
            "depends": [phases[-1]["dir"]],
            "paths": [],
            "status": "todo",
            "tasks": 0,
        }
    )
    write_phases(a, phases)
    return True


def _refuse_started(root, later):
    for p in later:
        folder = os.path.join(root, p["dir"])
        if p["status"] != "todo" or (os.path.isdir(folder) and os.listdir(folder)):
            raise PlanError(f"cannot insert before {p['dir']}: it has started (status {p['status']} or files in its folder)")


def insert_phase(a, phases, new, after):
    """Put `new` (its `dir` is set here) after phase `after` and renumber the later phases: rows, folders, depends. A closing `NN-finish` phase also depends on `new`. Refuses when a later phase has started (not `todo`, or files in its folder). -> the new list."""
    pos = next((i for i, p in enumerate(phases) if after in (p["dir"], p["dir"][3:])), None)
    if pos is None:
        raise PlanError(f"--after: unknown phase {after}")
    later = phases[pos + 1 :]
    root = os.path.join(feature_dir(a), "phases")
    _refuse_started(root, later)
    num = int(phases[pos]["dir"][:2]) + 1
    new["dir"] = f"{num:02d}-{new['dir']}"
    ren = {p["dir"]: f"{num + 1 + i:02d}-{p['dir'][3:]}" for i, p in enumerate(later)}
    for p in reversed(later):  # highest number first: no target name is taken yet
        if os.path.isdir(os.path.join(root, p["dir"])):
            os.rename(os.path.join(root, p["dir"]), os.path.join(root, ren[p["dir"]]))
    for p in phases:
        p["depends"] = [ren.get(d, d) for d in p["depends"]]
    for p in later:
        p["dir"] = ren.get(p["dir"], p["dir"])
        if is_finish_dir(p["dir"]) and new["dir"] not in p["depends"]:
            p["depends"] = [*p["depends"], new["dir"]]
    return [*phases[: pos + 1], new, *later]


def write_phases(a, phases):
    txt = read_text(plan_path(a)).split("## Phases")[0]
    rows = "".join(
        "| "
        + " | ".join([p["dir"], cell(p["goal"]), cell(p["done_when"]), ",".join(p["depends"]), ",".join(p["paths"]), p["status"], str(p.get("tasks", 0))])
        + " |\n"
        for p in phases
    )
    write_text(plan_path(a), txt + "## Phases\n" + PHASE_HEAD + rows)


def phase_ref(phases, name):
    """-> phase whose folder is `name` or `NN-name`'s suffix is `name`, else None."""
    return next((p for p in phases if name in (p["dir"], p["dir"][3:])), None)


def next_open_phase(phases):
    """The first phase that is not done and whose dependencies are all done, else None."""
    return next((p for p in phases if p["status"] != "done" and all((phase_ref(phases, d) or {}).get("status") == "done" for d in p["depends"])), None)


# ---- TASKS.md --------------------------------------------------------------


def started_tasks(d):
    """-> ids of the tasks with a dispatch or skip event in the stage's orch.jsonl."""
    return {e["task"] for e in read_jsonl(os.path.join(d, ORCH_LOG)) if e.get("ev") in STARTED_EVENTS and e.get("task")}


def tasks_path(d):
    return os.path.join(d, TASKS_FILE)


BULLET = re.compile(r"^- (?:\[[ xX]\] )?(task-\d+) (.*?)(?: - owner: (\w+))? - review: wave W(\d+)(?:, (\w+))?(?: - depends: (\S+))?\s*$")


def read_entries(d):
    """-> ordered list of {id, title, owner, wave, review, depends[]} from the TASKS.md bullets plan.py wrote."""
    out = []
    for line in read_text(tasks_path(d)).splitlines():
        m = BULLET.match(line)
        if m:
            out.append({"id": m[1], "title": m[2], "owner": m[3] or "", "wave": int(m[4]), "review": m[5] or "", "depends": csv(m[6])})
    return out


def bullet(e):
    dep = f" - depends: {','.join(e['depends'])}" if e["depends"] else ""
    return f"- {e['id']} {e['title']} - review: wave W{e['wave']}{dep}"


def graph_line(entries):
    waves = sorted({e["wave"] for e in entries})
    return " | ".join(f"wave {w}: " + ", ".join(e["id"] for e in entries if e["wave"] == w) for w in waves)


def _sections_after_graph(text):
    """-> the `## ...` sections that follow the Graph section of TASKS.md (for example `## Carried from earlier phases`), "" when none; `write_tasks` keeps them."""
    at = text.find("## Graph")
    if at < 0:
        return ""
    nxt = re.search(r"(?m)^## ", text[at + len("## Graph") :])
    return text[at + len("## Graph") + nxt.start() :].rstrip() + "\n" if nxt else ""


def write_tasks(d, entries):
    old = read_text(tasks_path(d))
    head = old.split("## Tasks")[0].rstrip() + "\n\n"
    body = "".join(bullet(e) + "\n" for e in sorted(entries, key=lambda e: int(e["id"][5:])))
    tail = _sections_after_graph(old)
    write_text(tasks_path(d), head + "## Tasks\n" + body + "\n## Graph\n" + graph_line(entries) + "\n" + ("\n" + tail if tail else ""))


def init_stage(d, stage, goal, flags, pr_title=""):
    title = f"pr-title: {pr_title.strip()}\n" if pr_title.strip() else ""
    write_text(tasks_path(d), f"# Stage: {stage}\nGoal: {goal.strip()}\n{title}flags: {flags.strip()}\n\n## Tasks\n\n## Graph\n")


PATH_ITEM_RE = re.compile(r"\s*(?:\"([^\"]*)\"|'([^']*)'|([^,]+))")  # a comma list where a quoted item may hold commas


LINE_EXTS = (".jsonl", ".ndjson", ".tsv", ".csv")  # line-based data files: git merges disjoint row edits cleanly
ANNOT_RE = re.compile(r"\s*\((rows|mass)\)\s*$", re.I)  # `path (rows)` = some rows only, `path (mass)` = most or all rows


def path_modes(text):
    """The `Predicted paths:` list of a task file's text -> [(path, mode)], mode `rows`, `mass` or "" (whole file, the default). Backticks dropped."""
    m = re.search(r"(?im)^Predicted paths:\s*(.*)$", text)
    if not m:
        return []
    out = []
    for a, b, c in PATH_ITEM_RE.findall(m[1]):
        item = (a or b or c).strip().strip("` ")
        am = ANNOT_RE.search(item)
        mode = am[1].lower() if am else ""
        path = ANNOT_RE.sub("", item).strip("` ")
        if path:
            out.append((path, mode))
    return out


def paths_in(text):
    """The `Predicted paths:` list of a task file's text (backticks and `(rows)`/`(mass)` marks dropped), [] when absent."""
    return [p for p, _ in path_modes(text)]


def is_line_based(path):
    return path.lower().endswith(LINE_EXTS)


def wave_conflict(m1, m2):
    """-> first (a, b) overlapping path pair of two path_modes lists that may NOT share a wave, else None. Allowed: the same line-based file with `(rows)` on both sides."""
    for a, ma in m1:
        for b, mb in m2:
            if overlap([a], [b]) and not (a.rstrip("/") == b.rstrip("/") and is_line_based(a) and ma == mb == "rows"):
                return a, b
    return None


AFFECTED_RE = re.compile(r"(?im)^Affected tests:[ \t]*(.*)$")


def task_affected_tests(text):
    """The `Affected tests:` line of a task file's text (tests that probably assert the old behavior), "" when absent."""
    m = AFFECTED_RE.search(text)
    v = m[1].strip() if m else ""
    return "" if v.lower().rstrip(".") == "none" else v


TASK_TYPES = ("code", "report", "webtest")


def task_type(text):
    """The `Type:` header of a task file's text, lowercased; `code` when absent. `report` = the deliverable is a file outside any repo (no diff); `webtest` = a browser check the web-tester agent runs (no dev, no worktree: the task's `Test:` validates its report on the stage branch checkout)."""
    m = re.search(r"(?im)^Type:[ \t]*(\S*)", text)
    return m[1].lower() if m else "code"


def task_inputs(text):
    """The `Inputs:` header lines of a task file's text, comma separated: [] when absent."""
    return [x.strip("` ") for m in re.finditer(r"(?im)^Inputs:[ \t]*(.*)$", text) for x in m[1].split(",") if x.strip("` ")]


def task_paths(d, tid):
    return paths_in(read_text(os.path.join(d, f"{tid}.md")))


def task_path_modes(d, tid):
    return path_modes(read_text(os.path.join(d, f"{tid}.md")))


TASK_FIELDS = {
    "title": None,
    "paths": "Predicted paths: ",
    "pattern": "Pattern: ",
    "test": "Test: ",
    "done": "Done: ",
    "turns": "turns <= ",
}


def set_task_field(txt, tid, field, value):
    """-> task-N.md text with one field replaced (title also updates the Goal line)."""
    if field == "title":
        txt = re.sub(r"(?m)^# " + tid + r": .*$", lambda m: f"# {tid}: {value}", txt, count=1)
        txt = re.sub(r"(?m)^Goal: .*$", lambda m: f"Goal: {value}", txt, count=1)
        return re.sub(r"(?m)^Goal: " + re.escape(value) + r"\n", "", txt, count=1)  # a Goal equal to the title is not written
    if field == "test" and "qrun.py" not in value:
        value = QRUN + value
    pre = TASK_FIELDS[field] or ""  # None only for "title", handled above
    pat = r"Predicted paths: " if field == "paths" else re.escape(pre)
    return re.sub(r"(?m)^" + pat + r".*$", lambda m: pre + value, txt, count=1)


def render_task(e, a, paths):
    cmd = a.test if "qrun.py" in a.test else QRUN + a.test
    pat = a.pattern or (paths[0] + ":1" if paths else "")
    aff = f"Affected tests: {a.affected}\n" if getattr(a, "affected", "") else ""
    return (
        f"# {e['id']}: {e['title']}\nUI: no\nWave: W{e['wave']}\n\n"  # no Goal line: it would repeat the title
        f"Predicted paths: {', '.join(paths)}\nPattern: {pat}\n{aff}\n"
        f"Test: {cmd}\nDone: {a.done}\nturns <= {a.turns}\nReply in 3 lines: files changed, test result, ESCALATE.\n"
    )
