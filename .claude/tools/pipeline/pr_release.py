"""Version bump and release of a stage branch: the release level, the bump commit, the release tag and the GitHub release. pr_ops.py re-exports these names."""

import os
import re
import tomllib
from pathlib import Path

from . import pr_base, pr_mode
from .configuration import GIT_NETWORK_TIMEOUT_S, PLAN_FILE, PROJECT_DEFAULTS, TASKS_FILE, WORK_DIR, release_branch
from .plan_files import section_body

LEVELS = ("patch", "minor", "major")


def _release_level(a_dir, stage):
    """-> highest `release: patch|minor|major` level over the stage TASKS.md and, for a phase stage, every phase TASKS.md of its feature (owner's word at planning); default patch."""
    work = os.path.join(a_dir, WORK_DIR)
    feature = stage.split("/phases/")[0]
    lists = [os.path.join(work, stage, TASKS_FILE)]
    pdir = os.path.join(work, feature, "phases")
    if os.path.isdir(pdir):
        lists += [os.path.join(pdir, n, TASKS_FILE) for n in sorted(os.listdir(pdir))]
    best = 0
    for tl in lists:
        text = Path(tl).read_text(encoding="utf-8") if os.path.isfile(tl) else ""
        for m in re.finditer(r"(?im)^\s*release\s*:\s*(patch|minor|major)\s*$", text):
            best = max(best, LEVELS.index(m[1].lower()))
    return LEVELS[best]


def _goal_version(a_dir, stage):
    """-> X.Y.Z named as `vX.Y.Z` in the first lines of the feature Plan.md (the owner's release goal), or ''."""
    plan = os.path.join(a_dir, WORK_DIR, stage.split("/phases/")[0], PLAN_FILE)
    if not os.path.isfile(plan):
        return ""
    head = "\n".join(Path(plan).read_text(encoding="utf-8").splitlines()[:12])
    m = re.search(r"\bv(\d+\.\d+\.\d+)\b", head)
    return m[1] if m else ""


def _ver(v):
    return tuple(int(n) for n in v.split("."))


_PIN = re.compile(
    r'(\{[^}\n]*?\bpath\s*=\s*"([^"\n]+)"[^}\n]*?\bversion\s*=\s*)"[^"\n]*"|(\{[^}\n]*?\bversion\s*=\s*)"[^"\n]*"([^}\n]*?\bpath\s*=\s*"([^"\n]+)")'
)


def _uses_workspace_version(manifest):
    if not os.path.isfile(manifest):
        return False
    try:
        data = tomllib.loads(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # unreadable or invalid TOML: not a workspace-version crate
        return False
    version = data.get("package", {}).get("version")
    return isinstance(version, dict) and version.get("workspace") is True


def _rewrite_pins(repo, cfg, new):
    """-> repo-relative manifests changed: every `version = "X"` next to a `path =` dependency whose target crate uses the workspace version is set to NEW."""
    name = cfg.get("pin_manifest", PROJECT_DEFAULTS["pin_manifest"])
    if not name:
        return []
    out = []
    for rel in pr_base._git(repo, "ls-files", "--", name, f"*/{name}").stdout.split():
        path = os.path.join(repo, rel)
        base = os.path.dirname(path)

        def sub(m, base=base) -> str:
            tgt = m[2] or m[5]
            if not _uses_workspace_version(os.path.join(base, tgt, name)):
                return m[0]
            return f'{m[1]}"{new}"' if m[1] else f'{m[3]}"{new}"{m[4]}'

        text = Path(path).read_text(encoding="utf-8")
        fixed = _PIN.sub(sub, text)
        if fixed != text:
            Path(path).write_text(fixed, encoding="utf-8")
            out.append(rel)
    return out


def _bump(v, level):
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise pr_base.Blocked(f"version {v!r} is not X.Y.Z: bump it by hand (or set flag no-release)")
    x, y, z = (int(n) for n in v.split("."))
    return f"{x + 1}.0.0" if level == "major" else f"{x}.{y + 1}.0" if level == "minor" else f"{x}.{y}.{z + 1}"


def _hook(repo, cfg, *args):
    """-> stdout of the project's `release_script` (pipeline.json, relative to the repo) run with ARGS; BLOCKED when it is missing or fails."""
    rs = cfg.get("release_script")
    path = os.path.join(repo, rs) if rs else ""
    if not rs or not os.path.isfile(path):
        raise pr_base.Blocked(f"release_script {rs!r} is not a file in {repo}: fix pipeline.json or set flag no-release")
    r = pr_base._run(["bash", path, *args], repo, 120)
    if r.returncode:
        raise pr_base.Blocked(f"release_script {' '.join(args[:1])} failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout.strip()


def _plan_bump(a_dir, stage, base, cur, level):
    """-> (old, new, how) for a fresh bump; how is '' when the files already sit at the Plan.md goal (new = goal)."""
    goal = _goal_version(a_dir, stage)
    if goal and _ver(goal) > _ver(base) and cur == goal:
        return cur, goal, ""
    if goal and _ver(goal) > _ver(cur):
        return cur, goal, "goal bump"
    return cur, _bump(cur, level), f"{level} bump"


def _release_ref(repo, cfg):
    """-> the ref to compare against: origin/<release branch> after a fresh fetch, else the local branch."""
    rname = release_branch(repo, cfg)
    if pr_base._git(repo, "remote", "get-url", "origin").returncode == 0:  # read the release head as origin has it now
        f = pr_base._git(repo, "-c", pr_mode.GH_CRED, "fetch", "-q", "origin", rname, timeout=GIT_NETWORK_TIMEOUT_S)
        if f.returncode:
            raise pr_base.Blocked(f"fetch of origin/{rname} failed: {(f.stderr or f.stdout).strip()[-200:]}")
    rel = f"origin/{rname}"
    if pr_base._git(repo, "rev-parse", "--verify", "-q", f"{rel}^{{commit}}").returncode:  # local-only repo: no remote ref, use the local release branch
        rel = rname
    return rel


def _bump_guard(repo, cfg, br):
    """-> the note when the stage branch adds nothing to the release branch, else None. Raises Blocked when the repo is off the stage branch or has uncommitted changes."""
    if pr_base._git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != br:
        raise pr_base.Blocked(f"{repo} is not on {br}: check out the stage branch, then retry version-bump")
    if pr_base._landed(repo, release_branch(repo, cfg), br, False) == "":
        return f"{br} adds nothing to {release_branch(repo, cfg)}: no version bump"
    if pr_base._git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise pr_base.Blocked(f"{repo} has uncommitted changes: commit or stash them, then retry version-bump")
    return None


def _version_bump(a_dir, repo, cfg, stage, br):
    """-> note. Bumps the version on the stage branch (last phase only, before the PR is marked ready) through the project's `release_script`: `current [REF]` and `set OLD NEW ENTRY`. Re-running completes a partial bump in a follow-up commit."""
    note = _bump_guard(repo, cfg, br)
    if note is not None:
        return note
    rel = _release_ref(repo, cfg)
    base = _hook(repo, cfg, "current", rel)
    cur = _hook(repo, cfg, "current")
    have = bool(pr_base._git(repo, "log", "--grep", r"^chore(release): v[0-9]", "--format=%s", "-1", f"{rel}..{br}").stdout.strip())
    level = _release_level(a_dir, stage)
    if have and cur != base:
        old, new, how = base, cur, "completed"  # a bump commit exists: the script reads the new version already
    else:
        old, new, how = _plan_bump(a_dir, stage, base, cur, level)
        if not how:
            return f"version files already at the goal v{new}: no second bump"
    return _write_bump(repo, cfg, a_dir, stage, br, old, new, how, have)


def _write_bump(repo, cfg, a_dir, stage, br, old, new, how, have):
    """-> note. Runs the `set` hook and the pin rewrite, then commits the version files."""
    files = [f for f in _hook(repo, cfg, "set", old, new, pr_base._merge_title(a_dir, stage, br)).splitlines() if f]
    files += [f for f in _rewrite_pins(repo, cfg, new) if f not in files]
    if not files:
        return f"version files already at v{new}"
    pr_base._git(repo, "add", "--", *files)
    r = pr_base._git(repo, "commit", "-m", f"chore(release): v{new}" + (" (complete version files)" if have else ""))
    if r.returncode:
        raise pr_base.Blocked(f"bump commit failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"{how} v{old} -> v{new} ({', '.join(files)})"


_SEMVER = r"\d+\.\d+\.\d+"


def _already_released(repo, cfg, rel, br):
    """-> the no-release note when nothing of the stage landed and the release head is already tagged, else None."""
    if pr_base._landed(repo, rel, br, False) != "":  # nothing of this stage landed: release only if the release branch head has no tag yet
        return None
    cur = _hook(repo, cfg, "current", rel)
    if re.fullmatch(_SEMVER, cur) and pr_base._git(repo, "rev-parse", "-q", "--verify", f"refs/tags/v{cur}").returncode == 0:
        return f"{br} adds nothing to {rel} and v{cur} is already tagged: no release"
    return None


def _merged_sha(repo, br, rel):
    """-> the squash SHA of the merged PR of br, fetched when it is not local."""
    sha = (pr_base._view(repo, br, "mergeCommit") or {}).get("oid")
    if pr_base._view(repo, br, "state") != "MERGED" or not sha:
        raise pr_base.Blocked(f"the PR of {br} is not MERGED: finish master-merged first")
    if pr_base._git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode:  # the squash was made on GitHub: fetch it before tagging
        f = pr_base._git(repo, "-c", pr_mode.GH_CRED, "fetch", "-q", "origin", rel, timeout=GIT_NETWORK_TIMEOUT_S)
        if f.returncode:
            raise pr_base.Blocked(f"fetch of origin/{rel} failed, the merge commit {sha[:8]} is not local: {(f.stderr or f.stdout).strip()[-200:]}")
    return sha


def _ensure_tag(repo, tag, sha):
    """Create the annotated tag on sha; an existing tag on another commit blocks."""
    if pr_base._git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0:
        if pr_base._git(repo, "rev-parse", f"{tag}^{{commit}}").stdout.strip() != sha:
            raise pr_base.Blocked(f"tag {tag} already exists on another commit: resolve by hand, then skip release")
        return
    t = pr_base._git(repo, "tag", "-a", tag, sha, "-m", tag)
    if t.returncode:
        raise pr_base.Blocked(f"git tag failed: {(t.stderr or t.stdout).strip()[-300:]}")


def _github_release(repo, tag, ver, sha):
    """Push the tag and create the GitHub release (notes from the CHANGELOG section) -> note."""
    p = pr_base._git(repo, "-c", pr_mode.GH_CRED, "push", "origin", tag, timeout=GIT_NETWORK_TIMEOUT_S)
    if p.returncode:
        raise pr_base.Blocked(f"push of {tag} failed: {(p.stderr or p.stdout).strip()[-300:]}")
    cl = Path(repo, "CHANGELOG.md")
    notes = "--generate-notes"
    body = ""
    if cl.is_file():
        body = (section_body(cl.read_text(encoding="utf-8"), lambda ln: ln.startswith(f"## [{ver}]"), "## [") or "").strip()
    args = ["gh", "release", "create", tag, "--verify-tag", "--title", tag] + (["--notes", body] if body else [notes])
    if pr_base._run(["gh", "release", "view", tag], repo).returncode == 0:
        return f"tagged {tag} on {sha[:8]}; release already exists"
    r = pr_base._run(args, repo, 300)
    if r.returncode:
        raise pr_base.Blocked(f"gh release create failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"released {tag} on {sha[:8]}"


def _release(a_dir, repo, cfg, stage, br, pr):
    """-> note. Tags the landed commit vX.Y.Z (the squash SHA of the merged PR; the release branch head for a local repo), pushes the tag and creates the GitHub release."""
    rel = release_branch(repo, cfg)
    if note := _already_released(repo, cfg, rel, br):
        return note
    sha = _merged_sha(repo, br, rel) if pr else pr_base._git(repo, "rev-parse", rel).stdout.strip()
    ver = _hook(repo, cfg, "current", sha)  # the version the landed commit holds
    if not re.fullmatch(_SEMVER, ver):
        raise pr_base.Blocked(f"release_script current {sha[:8]} printed {ver!r}, not X.Y.Z")
    tag = f"v{ver}"
    _ensure_tag(repo, tag, sha)
    if not pr:
        return f"tagged {tag} on {sha[:8]} (local-only repo, no release)"
    return _github_release(repo, tag, ver, sha)
