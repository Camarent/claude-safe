#!/usr/bin/env python3
"""final_check: the stage's final verification by script, no model. Replaces the tester when the project sets `gates`.

    python3 -I /root/.claude/tools/audit/final_check.py --dir WORKSPACE --stage S

Runs, in the repo folder, every command of `gates` in `<workspace>/pipeline.json` (each as one shell command) and then every `each` check:
`"each": [{"glob": "workspace/*/course.json", "cmd": "my-validator {dir} --strict"}]` runs `cmd` once per match, `{dir}` = the match's folder.
A gate or an `each` entry may set `"parallel": "<group>"`: all commands of one group run concurrently (at most GATE_GROUP_MAX_WORKERS at a time), so a group must never share a build dir.
Each command goes through qrun (tiny summary or failure excerpts). `test_env` is exported. All commands run even after a failure so one report shows everything;
`VERDICT: DONE` only when all pass, else `VERDICT: FAIL` with the failing commands first."""

import argparse
import contextlib
import glob
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from pipeline.configuration import (
    CLEAR_WHY_BAD_RC,
    CLEAR_WHY_GREEN,
    CLEAR_WHY_NO_CMD,
    EV_GATE_CMD,
    FAIL_ID_RES,
    FAIL_IDS_SHOWN,
    GATE_GROUP_MAX_WORKERS,
    GATE_PARALLEL_KEY,
    ConfigError,
    each_of,
    final_gates_of,
    gates_of,
    merge_gates_of,
    project,
    repo_dir,
    test_env,
)
from pipeline.util import append_jsonl, script_timeout

QRUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qrun.py")


def _gates(cfg, merge, final=False):
    """-> the gates to run: `gates`, plus `gates_merge` when MERGE (the merge gate, the preflight and the final test; never a task's gates), plus `gates_final` when FINAL (the final test only)."""
    return [*gates_of(cfg), *(merge_gates_of(cfg) if merge else []), *(final_gates_of(cfg) if final else [])]


def commands(cfg, repo, merge=False, final=False):
    """-> [shell command] from `gates`, then `gates_merge` when MERGE, then `gates_final` when FINAL, then `each`."""
    return [g["cmd"] for g in _gates(cfg, merge, final)] + [c for c, _ in _each_cmds(cfg, repo)]


def _each_cmds(cfg, repo):
    """-> [(expanded `each` command, group or None)] in config order."""
    out = []
    for e in each_of(cfg):
        for hit in sorted(glob.glob(os.path.join(repo, e["glob"]))):
            out.append((e["cmd"].replace("{dir}", shlex.quote(os.path.relpath(os.path.dirname(hit), repo))), e.get(GATE_PARALLEL_KEY)))
    return out


def gate_names(cfg, merge=False, final=False):
    """-> {gate cmd: gate name}; empty without `gates`. MERGE adds the `gates_merge` ones, FINAL the `gates_final` ones."""
    return {g["cmd"]: g["name"] for g in _gates(cfg, merge, final)}


def gate_groups(cfg, repo=None, merge=False, final=False):
    """-> {cmd: parallel group}; only gates (and, with REPO, the `each` commands expanded in it) that name a group. A repeated command keeps its first group."""
    groups = {g["cmd"]: g[GATE_PARALLEL_KEY] for g in _gates(cfg, merge, final) if g.get(GATE_PARALLEL_KEY)}
    if repo is not None:
        for c, grp in _each_cmds(cfg, repo):
            if grp:
                groups.setdefault(c, grp)
    return groups


def _timeout():
    """Seconds one command may run: env ORCH_SCRIPT_TIMEOUT, default SCRIPT_TIMEOUT_S (as qrun.py, which kills its process group at that limit; the +60 s limit here is the backstop for qrun itself)."""
    return script_timeout()


def run_group(cmd, **kw):
    """subprocess.run in its own session; on timeout the whole process group is killed, not only the child."""
    timeout = kw.pop("timeout", None)
    kw.pop("capture_output", None)
    kw.pop("text", None)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, **kw) as p:
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                p.kill()
            p.communicate()
            raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _fetch_failed(rc, text):
    """True when a red gate's output shows a network/db fetch failure (cargo-audit advisory-db), not a real finding."""
    from pipeline.configuration import FETCH_FAIL_MARKS

    return rc != 0 and any(m in text for m in FETCH_FAIL_MARKS)


def _run_one(c, repo, env, runner):
    """-> (cmd, rc, secs, text); a red run caused by a fetch failure is retried FETCH_RETRIES times, then labelled FETCH_FAIL_LINE."""
    from pipeline.configuration import FETCH_FAIL_LINE, FETCH_RETRIES

    res = _run_once(c, repo, env, runner)
    for _ in range(FETCH_RETRIES):
        if not _fetch_failed(res[1], res[3]):
            return res
        res = _run_once(c, repo, env, runner)
    if _fetch_failed(res[1], res[3]):
        res = (*res[:3], f"{FETCH_FAIL_LINE}\n{res[3]}")
    return res


def _run_once(c, repo, env, runner):
    t0 = time.time()
    try:
        r = runner([sys.executable, "-I", QRUN, "--", "sh", "-c", c], cwd=repo, env=env, capture_output=True, text=True, timeout=_timeout() + 60)
        rc, text = r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        rc, text = 124, f"timed out after {_timeout():g}s (ORCH_SCRIPT_TIMEOUT)"
    except OSError as e:
        rc, text = 127, f"cannot run: {e}"
    return (c, rc, round(time.time() - t0), text)


def _units(cmds, groups):
    """-> [[index]] in config order; commands of one group run together at the position of the group's first member."""
    units: list[list[int]] = []
    at: dict[str, int] = {}
    for i, c in enumerate(cmds):
        g = groups.get(c)
        if g and g in at:
            units[at[g]].append(i)
            continue
        if g:
            at[g] = len(units)
        units.append([i])
    return units


def run_all(cmds, repo, env, runner=run_group, groups=None):
    """-> [(cmd, returncode, seconds, qrun text)] in config order. Every command runs, red or not, so the report lists all reds together (`report` names the gates).
    Commands in the same GROUPS ({cmd: group}) entry run concurrently; output is collected per command, so results stay deterministic."""
    out: list[tuple[int, tuple]] = []  # (config index, result)
    for unit in _units(cmds, groups or {}):
        if len(unit) == 1:
            res = [_run_one(cmds[unit[0]], repo, env, runner)]
        else:
            with ThreadPoolExecutor(max_workers=min(len(unit), GATE_GROUP_MAX_WORKERS)) as pool:
                res = list(pool.map(lambda i: _run_one(cmds[i], repo, env, runner), unit))
        out.extend(zip(unit, res, strict=True))
    return [r for _, r in sorted(out, key=lambda x: x[0])]


def log_gate_cmds(path, kind, results, names, task="", base_red=()):
    """Append one `gate_cmd` metrics record per command of RESULTS ([(cmd, rc, seconds, text)]) to the metrics file PATH: KIND (`task` or `merge`), the gate name, exit code, seconds, and `base_red` when the command was red on the stage base too (its `rc` is then 0: it was cleared, not the task's failure). A failed write is ignored: a metric never stops a gate."""
    with contextlib.suppress(OSError):
        for cmd, rc, secs, _text in results:
            rec = {"ev": EV_GATE_CMD, "ts": time.time(), "kind": kind, "task": task, "gate": names.get(cmd, cmd), "rc": rc, "seconds": round(secs, 1)}
            append_jsonl(path, {**rec, "base_red": True} if cmd in base_red else rec)


WARN_MARKER = r"(?i)\bwarn(ing)?s?\b"
WARN_BAD_RC = (124, 127)  # timeout, cannot run: never a warning


def _warn_rule(stage, e):
    """-> (gate name, {"marker", "exit"}) of one gate_warn_ok entry; ConfigError on a bad type."""
    d = {"name": e} if isinstance(e, str) else e
    codes = d.get("exit", []) if isinstance(d, dict) else None
    if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not isinstance(codes, list) or not all(type(x) is int for x in codes):
        raise ConfigError(f"pipeline.json: gate_warn_ok[{stage!r}]: entries are a gate name or {{name, marker, exit: [int]}}")
    marker = d.get("marker", WARN_MARKER)
    if not isinstance(marker, str):
        raise ConfigError("pipeline.json: gate_warn_ok `marker` must be a string")
    try:
        re.compile(marker)
    except re.error as err:
        raise ConfigError(f"pipeline.json: gate_warn_ok `marker` is not a regex: {err}") from err
    return d["name"], {"marker": marker, "exit": codes}


def warn_ok_rules(cfg, stage):
    """-> ({gate name: {"marker": regex, "exit": [int]}}, [note]) for STAGE from pipeline.json `gate_warn_ok` = {stage: [name | {"name", "marker", "exit"}]}.
    For a gate that exits non-zero on warnings (a `--strict` validator) when the stage itself adds the rule that warns. The key "*" is refused (noted, ignored): a gate must be listed per stage.
    Types are checked here once; a bad entry raises ConfigError."""
    raw = cfg.get("gate_warn_ok") or {}
    if not isinstance(raw, dict):
        raise ConfigError("pipeline.json: `gate_warn_ok` must be an object {stage: [gate]}")
    notes = ['gate_warn_ok key "*" ignored: list the gate under each stage'] if "*" in raw else []
    entries = raw.get(stage) or []
    if not isinstance(entries, list):
        raise ConfigError(f"pipeline.json: gate_warn_ok[{stage!r}] must be a list")
    rules = dict(_warn_rule(stage, e) for e in entries)
    return rules, notes


def _is_warning(rc, text, rule):
    if rc <= 0 or rc in WARN_BAD_RC:
        return False
    return rc in rule["exit"] or bool(re.search(rule["marker"], text))


def accept_warnings(results, names, rules):
    """-> (results with the gates accepted as plain warnings turned green, [(gate name, output tail)]).
    NAMES = gate names in the order of the first results (gates come first in config order, so two gates may share a cmd)."""
    out, accepted = [], []
    for i, (c, rc, secs, text) in enumerate(results):
        name = names[i] if i < len(names) else None
        ok = name in rules and _is_warning(rc, text, rules[name])
        if ok:
            accepted.append((name, "\n".join(text.splitlines()[-25:])))
        out.append((c, 0 if ok else rc, secs, text))
    return out, accepted


def clear_base_red(results, repo, env, groups=None, runner=run_group, base_cmds=None):
    """-> (results with every red command that is also red on REPO (the stage base) turned green, [those commands]). Only the red commands run again, there: a red the base already has is not the task's doing.
    Only a command the base has too (BASE_CMDS, e.g. an `each` command for a folder the task added is not) and that really ran (not a timeout or `cannot run`, WARN_BAD_RC, on either side) is cleared.
    A command that is red in both places counts as the base's, whatever the failing lines are."""
    red = [c for c, rc, _s, _t in results if rc != 0 and rc not in WARN_BAD_RC and (base_cmds is None or c in base_cmds)]
    if not red:
        return results, []
    again = run_all(red, repo, env, runner=runner, groups=groups)
    same = {c for c, rc, _s, _t in again if rc != 0 and rc not in WARN_BAD_RC}
    return [(c, 0 if c in same else rc, secs, text) for c, rc, secs, text in results], [c for c in red if c in same]


def failing_ids(text):
    """-> the failing test ids a gate's output names (FAIL_ID_RES), in order, unique, at most FAIL_IDS_SHOWN."""
    ids = dict.fromkeys(m for rx in FAIL_ID_RES for m in re.findall(rx, text))
    return list(ids)[:FAIL_IDS_SHOWN]


def rerun_red(results, repo, env, groups=None, runner=run_group):
    """-> (results with every red command that is green on a second, unchanged run in REPO turned green, {flaky command: failing test ids of its first run}). Each red command runs once more; a timeout or `cannot run` (WARN_BAD_RC) is never rerun."""
    red = [c for c, rc, _s, _t in results if rc != 0 and rc not in WARN_BAD_RC]
    if not red:
        return results, {}
    green = {c for c, rc, _s, _t in run_all(red, repo, env, runner=runner, groups=groups) if rc == 0}
    flaky = {c: failing_ids(t) for c, rc, _s, t in results if c in green and rc != 0}
    return [(c, 0 if c in flaky else rc, secs, text) for c, rc, secs, text in results], flaky


def uncleared_reasons(results, cleared, base_cmds, base_problem=""):
    """-> {red command: why the base check did not clear it}: a timeout or `cannot run` is never cleared; a command the stage base has not; BASE_PROBLEM (the main checkout is no fair base); else the command is green on the base, so the failure is the task's."""
    out = {}
    for c, rc, _s, _t in results:
        if rc == 0 or c in cleared:
            continue
        if rc in WARN_BAD_RC:
            out[c] = CLEAR_WHY_BAD_RC.format(rc=rc)
        elif base_cmds is not None and c not in base_cmds:
            out[c] = CLEAR_WHY_NO_CMD
        elif base_problem:
            out[c] = base_problem
        else:
            out[c] = CLEAR_WHY_GREEN
    return out


def report(results, gates=None):
    """VERDICT text. A failing gate (GATES = {cmd: name}) is reported as `gate <name>: FAILED` with the output tail."""
    gates = gates or {}
    bad = [r for r in results if r[1] != 0]
    lines = [f"VERDICT: {'FAIL' if bad else 'DONE'}", f"{len(results) - len(bad)} of {len(results)} commands passed"]
    for c, rc, secs, text in bad + [r for r in results if r[1] == 0]:
        if rc and c in gates:
            lines.append(f"gate {gates[c]}: FAILED ({secs}s) {c}\n" + "\n".join("  " + line for line in text.splitlines()[-25:]))
        else:
            lines.append(f"{'FAIL' if rc else 'ok'} ({secs}s) {c}" + ("\n" + "\n".join("  " + line for line in text.splitlines()[:25]) if rc else ""))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--stage", default="")
    a = ap.parse_args(argv)
    cfg = project(a.dir)
    repo = repo_dir(a.dir, cfg)
    cmds = commands(cfg, repo, merge=True, final=True)
    if not cmds:
        print("VERDICT: FAIL\nno `gates`, `gates_merge` or `gates_final` in pipeline.json: nothing to run")
        return 0
    gates = gate_names(cfg, merge=True, final=True)
    results = run_all(cmds, repo, {**os.environ, **test_env(cfg, a.dir)}, groups=gate_groups(cfg, repo, merge=True, final=True))
    print(report(results, gates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
