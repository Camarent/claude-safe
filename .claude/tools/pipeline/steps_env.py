"""Environment repair: a red test run or red task gates that name a missing environment piece (module, config, command) get the job worktree's `worktree_setup` rerun once, then the red step runs again. An environment red is no dev try; a second one goes to the lead."""

from .configuration import ENV_REPAIR_MAX, EV_DISPATCH, tool_cmd

ENV_REPAIR_WHY = "environment repair (worktree_setup rerun in the job worktree)"  # `why` prefix of the repair dispatch
TEST_KIND, GATES_KIND = "test run", "task gates"  # the red step that runs again after the repair


def _repair_events(events, tid):
    return [e for e in events if e.get("task") == tid and e["ev"] == EV_DISPATCH and (e.get("why") or "").startswith(ENV_REPAIR_WHY)]


def env_repair(a, tid, rid, kind, events):
    """-> the repair dispatch (a script step) while the task has repairs left, else None. EVENTS: the whole orch log, so the count survives a replay."""
    if len(_repair_events(events, tid)) >= ENV_REPAIR_MAX:
        return None
    return {
        "action": "dispatch",
        "id": rid,
        "agent": "script",
        "model": None,
        "task": tid,
        "role": "script",
        "why": f"{ENV_REPAIR_WHY}: {kind}",
        "prompt": f"{tool_cmd('testrun.py')} --stage {a.stage} --task {tid} --dir {getattr(a, 'dir', '.')} --repair-env",
    }


def env_repair_kind(mine, rid):
    """The red step (TEST_KIND or GATES_KIND) that the repair dispatch `rid` was made for."""
    why = next((e.get("why") or "" for e in mine if e["ev"] == EV_DISPATCH and e.get("id") == rid), "")
    return TEST_KIND if why.endswith(TEST_KIND) else GATES_KIND
