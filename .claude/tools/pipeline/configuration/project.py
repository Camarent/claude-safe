"""Per-project settings: project() loads <workspace>/pipeline.json over PROJECT_DEFAULTS; gates, quality tools, repo folders, test env."""

import copy
import json
import os
import re

from .defaults import PROJECT_DEFAULTS
from .env import ENV_LEAD_LOG, QUALITY_TOOLS
from .errors import ConfigError
from .gitbranch import GIT_DETECTED_CACHE, GIT_DIRS_CACHE, detected_release_branch
from .layout import PUBLISH_KEY
from .publish import publish_entries


def test_env(cfg, workspace):
    """-> cfg `test_env` with every value made absolute against the repo folder, so a later `cd` in a command cannot change what "." means."""
    repo = repo_dir(workspace, cfg)
    return {k: os.path.normpath(os.path.join(os.path.abspath(repo), str(v))) for k, v in (cfg.get("test_env") or {}).items()}


GATE_PARALLEL_KEY = "parallel"
GATES_KEY = "gates"
MERGE_GATES_KEY = "gates_merge"  # gates that run only at the merge gate, the red-base preflight and the final test, never in a task's job worktree
REGISTRY_FILES_KEY = "registry_files"  # pipeline.json: [{"path", "when"}]: a task whose text matches the regex `when` must list `path` in its Predicted paths (a registry file many tasks append to)
FINAL_GATES_KEY = "gates_final"  # gates that run only in the final test (final_check.py): once per stage, never in a task, at a wave merge or in the preflight


def gates_of(cfg, key=GATES_KEY):
    """-> [{"name", "cmd"}] from cfg `gates` (validated: a list, unique names, non-empty cmds); [] when absent. A gate's optional string GATE_PARALLEL_KEY (group name) is kept; gates of one group run concurrently, so a group must never share a build dir."""
    raw = cfg.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"pipeline.json: `{key}` must be a list of {{name, cmd}} objects")
    seen, out = set(), []
    for g in raw:
        name = str(g.get("name") or "").strip() if isinstance(g, dict) else ""
        cmd = str(g.get("cmd") or "").strip() if isinstance(g, dict) else ""
        if not name:
            raise ConfigError(f"pipeline.json: every `{key}` entry needs a non-empty `name`")
        if name in seen:
            raise ConfigError(f"pipeline.json: duplicate gate name `{name}` in `{key}`")
        if not cmd:
            raise ConfigError(f"pipeline.json: `{key}` entry `{name}` has an empty `cmd`")
        group = g.get(GATE_PARALLEL_KEY)
        if group is not None and not isinstance(group, str):
            raise ConfigError(f"pipeline.json: `{key}` entry `{name}`: `{GATE_PARALLEL_KEY}` must be a string")
        seen.add(name)
        out.append({"name": name, "cmd": cmd, **({GATE_PARALLEL_KEY: group.strip()} if group and group.strip() else {})})
    return out


def merge_gates_of(cfg):
    """-> the `gates_merge` entries (same shape as `gates`): they run at the merge gate and the final test only."""
    return gates_of(cfg, MERGE_GATES_KEY)


def final_gates_of(cfg):
    """-> the `gates_final` entries (same shape as `gates`): they run in the final test only."""
    return gates_of(cfg, FINAL_GATES_KEY)


def all_gates_of(cfg):
    """-> `gates`, then `gates_merge`, then `gates_final`: every gate the project has, wherever it runs."""
    return [*gates_of(cfg), *merge_gates_of(cfg), *final_gates_of(cfg)]


def driver_gates_on(cfg):
    """True when the driver runs the project's gates + each once per task: the project defines some and jobs have worktrees (never the main checkout)."""
    return bool(cfg.get("worktrees") and (gates_of(cfg) or each_of(cfg)))


def each_of(cfg):
    """-> cfg `each` entries; an optional GATE_PARALLEL_KEY must be a string (kept stripped, dropped when blank). Other keys are left as they are."""
    out = []
    for i, e in enumerate(cfg.get("each") or []):
        group = e.get(GATE_PARALLEL_KEY) if isinstance(e, dict) else None
        if group is not None and not isinstance(group, str):
            raise ConfigError(f"pipeline.json: each[{i}]: `{GATE_PARALLEL_KEY}` must be a string")
        rest = {k: v for k, v in e.items() if k != GATE_PARALLEL_KEY}
        out.append({**rest, **({GATE_PARALLEL_KEY: group.strip()} if group and group.strip() else {})})
    return out


def lead_logs(workspace):
    """-> lead transcript paths for the feature retro: env ENV_LEAD_LOG (os.pathsep list) wins, else pipeline.json `lead_log` (string or list), else []. The pipeline cannot discover the lead session itself."""
    env = os.environ.get(ENV_LEAD_LOG)
    if env:
        return [p for p in env.split(os.pathsep) if p]
    cfg = project(workspace).get("lead_log") or []
    return [cfg] if isinstance(cfg, str) else [str(p) for p in cfg]


_PROJECT_CACHE: dict[
    tuple[str, tuple[int, int] | None], dict
] = {}  # (abs workspace, pipeline.json (mtime_ns, size) or None) -> project() result; see clear_cache()


def clear_cache():
    """Forget every memoized project() result and the git lookups. Call after changing a repo's branches or HEAD, or a workspace's git config, in the same process (tests do)."""
    _PROJECT_CACHE.clear()
    GIT_DIRS_CACHE.clear()
    GIT_DETECTED_CACHE.clear()


def project(workspace):
    """-> PROJECT_DEFAULTS overlaid with <workspace>/pipeline.json (unknown keys kept; a missing file means defaults, a broken one raises ConfigError).
    An invalid `gates`, or a legacy `full_test` key, raises ConfigError.
    Memoized per process on the workspace and the pipeline.json mtime/size: the pipeline.json part only. The release branch detected from git is memoized on the git state signature (detected_release_branch), so repo HEAD/branch changes show at once. Returns a fresh copy."""
    path = os.path.join(workspace, "pipeline.json")
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        sig = None
    key = (os.path.abspath(workspace), sig)
    if key not in _PROJECT_CACHE:
        _PROJECT_CACHE[key] = _load_project(workspace)
    cfg = copy.deepcopy(_PROJECT_CACHE[key])
    if not cfg.get("branch_release"):  # git-derived: re-checked against the git state on every call, the repo HEAD or branches may change in-process
        cfg["branch_release"] = detected_release_branch(repo_dir(workspace, cfg), cfg)
        cfg["_release_detected"] = True
    return cfg


def _load_project(workspace):
    cfg = dict(PROJECT_DEFAULTS)
    path = os.path.join(workspace, "pipeline.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = None
    except ValueError as e:
        raise ConfigError(f"{path}: not valid JSON ({e})") from e
    if isinstance(data, dict):
        cfg.update(data)
    if "full_test" in cfg:
        raise ConfigError('pipeline.json: `full_test` is no longer supported; move its commands into `gates` ([{"name": ..., "cmd": ...}])')
    if "gates" in cfg:
        cfg["gates"] = gates_of(cfg)
    _check_gate_lists(cfg)
    if REGISTRY_FILES_KEY in cfg:
        cfg[REGISTRY_FILES_KEY] = registry_files_of(cfg)
    each_of(cfg)
    cfg[PUBLISH_KEY] = publish_entries(cfg)
    return cfg


def registry_files_of(cfg):
    """-> [{"path", "when"}] from cfg `registry_files` (validated: a list of objects with a non-empty `path` and a `when` that is a valid regex); [] when absent."""
    raw = cfg.get(REGISTRY_FILES_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"pipeline.json: `{REGISTRY_FILES_KEY}` must be a list of {{path, when}} objects")
    out = []
    for e in raw:
        path = str(e.get("path") or "").strip() if isinstance(e, dict) else ""
        when = str(e.get("when") or "") if isinstance(e, dict) else ""
        if not path or not when:
            raise ConfigError(f"pipeline.json: every `{REGISTRY_FILES_KEY}` entry needs a `path` and a `when` regex")
        try:
            re.compile(when)
        except re.error as err:
            raise ConfigError(f"pipeline.json: `{REGISTRY_FILES_KEY}` entry `{path}`: `when` is not a valid regex ({err})") from err
        out.append({"path": path, "when": when})
    return out


def _check_gate_lists(cfg):
    """Validate `gates_merge` and `gates_final` and refuse a gate name that sits in two of the three lists."""
    keys = (GATES_KEY, MERGE_GATES_KEY, FINAL_GATES_KEY)
    for key in keys[1:]:
        if key in cfg:
            cfg[key] = gates_of(cfg, key)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            twice = {g["name"] for g in gates_of(cfg, a)} & {g["name"] for g in gates_of(cfg, b)}
            if twice:
                raise ConfigError(f"pipeline.json: gate name(s) {', '.join(sorted(twice))} in both `{a}` and `{b}`: names must be unique")


def quality_tools(cfg):
    """-> {extension: tuple of tool names}: QUALITY_TOOLS, or the project's `quality_tools` (it replaces the defaults)."""
    table = cfg.get("quality_tools")
    if table is None:
        return dict(QUALITY_TOOLS)
    if not isinstance(table, dict) or not all(isinstance(v, list) and all(isinstance(t, str) for t in v) for v in table.values()):
        raise ConfigError('pipeline.json: `quality_tools` must map an extension to a list of tool names, e.g. {".py": ["ruff"]}')
    return {k: tuple(v) for k, v in table.items()}


def repo_dir(workspace, cfg):
    """-> the project's repo folder: <workspace>/<cfg repo>, or WORKSPACE when `repo` is unset."""
    return os.path.join(workspace, cfg["repo"]) if cfg.get("repo") else workspace


def repo_dirs(workspace, cfg):
    """-> every repo folder of the project: the `repos` list (below WORKSPACE), else just repo_dir."""
    names = cfg.get("repos") or []
    return [os.path.join(workspace, n) for n in names] if names else [repo_dir(workspace, cfg)]


def repo_path(workspace):
    """-> the repo folder of the project in WORKSPACE (validated by project())."""
    return repo_dir(workspace, project(workspace))
