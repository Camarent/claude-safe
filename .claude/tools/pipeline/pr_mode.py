"""Which branch/PR script steps apply to a run: pushed repos get the PR steps, repos with a derived work branch get the cleanup step."""

import os
import re
import shlex
import shutil
import subprocess
from urllib.parse import urlparse

from . import gitx
from .configuration import GIT_MIN_VERSION, GIT_SAFE_ENV, GIT_TIMEOUT_S, GITHUB_HOSTS, project, repo_path, tool_cmd, tool_setting, work_branch

GH_CRED = "credential.helper=!gh auth git-credential"


_git = gitx.try_run  # tests patch pr_mode._git


def _run(args, cwd):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_SAFE_ENV}, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def derived(a):
    """True when the work branch comes from the `{feature}` template (a fixed `branch_work` keeps the old flow)."""
    return "{feature}" in (project(a.dir).get("branch_work") or "{feature}")


def is_git(a):
    r = _git(repo_path(a.dir), "rev-parse", "--git-dir")
    return bool(r and r.returncode == 0)


def has_origin(a):
    r = _git(repo_path(a.dir), "remote", "get-url", tool_setting("GIT_REMOTE"))
    return bool(r and r.returncode == 0)


GIT_SUFFIX = ".git"
SLUG_PARTS = 2  # owner/name


def parse_remote(url):
    """-> (lowercase host, 'owner/name') of a network remote URL (scheme://[user@]host[:port]/owner/name, scp-style [user@]host:owner/name, optional .git), ('', '') otherwise."""
    if "://" in url:
        u = urlparse(url)
        host, path = u.hostname or "", u.path
    else:
        host, _, path = url.rpartition("@")[2].partition(":")
    path = path.strip("/").removesuffix(GIT_SUFFIX)
    parts = path.split("/")
    if not host or len(parts) != SLUG_PARTS or not all(parts):
        return "", ""
    return host.lower(), path


def origin_url(repo):
    r = _git(repo, "remote", "get-url", tool_setting("GIT_REMOTE"))
    return r.stdout.strip() if r and r.returncode == 0 else ""


def origin_parts(repo):
    """-> (host, 'owner/name') of the `origin` URL, ('', '') when it is no network URL (a local path)."""
    url = origin_url(repo)
    return parse_remote(url) if url and not url.startswith(("/", ".")) else ("", "")


def github_origin(a):
    """True when `origin` is on a GitHub host (`github_hosts` in pipeline.json, default GITHUB_HOSTS; an empty list turns the check off)."""
    hosts = project(a.dir).get("github_hosts", GITHUB_HOSTS)
    host = origin_parts(repo_path(a.dir))[0]  # '' = a local path or unparsable URL: no host to reject
    return not hosts or not host or host in [h.lower() for h in hosts]


def git_too_old(repo):
    """-> 'git X.Y is older than A.B' when the installed git lacks `merge-tree --write-tree`, else ''."""
    r = _git(repo, "version")
    m = re.search(r"(\d+)\.(\d+)", r.stdout) if r and r.returncode == 0 else None
    if m and (int(m[1]), int(m[2])) < GIT_MIN_VERSION:
        return f"git {m[1]}.{m[2]} is older than {GIT_MIN_VERSION[0]}.{GIT_MIN_VERSION[1]}"
    return ""


def down_reason(a):
    """Why the PR flow is off although wanted: names the cause (never silent)."""
    if not github_origin(a):
        return f"`origin` is not on a GitHub host ({', '.join(project(a.dir).get('github_hosts', GITHUB_HOSTS))}); set `github_hosts` in pipeline.json for GitHub Enterprise"
    return "`gh` is missing or not logged in (`gh auth status` fails)"


_GH_OK: set[str] = set()  # repo paths whose `gh auth status` passed in this process: a later probe that cannot run does not undo it


def gh_auth(a):
    """-> True when `gh auth status` passes, False on an explicit auth failure, None when the probe could not run (timeout, network blip). A pass is cached per repo."""
    key = repo_path(a.dir)
    if key in _GH_OK:
        return True
    if shutil.which("gh") is None:  # not installed: an explicit failure, not a blip
        return False
    r = _run(["gh", "auth", "status"], key)
    if r is None:
        return None
    if r.returncode == 0:
        _GH_OK.add(key)
        return True
    return False


def pr_active(a):
    """PR steps run: derived work branch, a GitHub `origin`, `gh auth status` green and no `"pr": false` in pipeline.json."""
    if getattr(a, "dry_run", False) or project(a.dir).get("pr") is False or not derived(a) or not has_origin(a) or not github_origin(a):
        return False
    return gh_auth(a) is True


def direct_flow(a):
    """True when the stage branch is merged locally (and pushed directly): the repo has no `origin`, or pipeline.json sets `"pr": false`. A repo with an `origin` never drops into this flow because `gh auth status` failed for a moment."""
    return project(a.dir).get("pr") is False or not has_origin(a)


def gh_down(a):
    """True when the PR flow is wanted (derived branch, an `origin`, no `"pr": false`) and `gh auth status` explicitly fails: nothing merges the stage branch, the lead must. A probe that could not run is not a failure."""
    return not getattr(a, "dry_run", False) and project(a.dir).get("pr") is not False and derived(a) and has_origin(a) and gh_auth(a) is False


def sweep_active(a):
    """deps-sweep runs: derived work branch, an `origin`, `gh auth status` green and no `"deps_sweep": false` in pipeline.json."""
    if getattr(a, "dry_run", False) or project(a.dir).get("deps_sweep") is False or not derived(a) or not has_origin(a):
        return False
    return gh_auth(a) is True


def sweep_cmd(a):
    return f"{tool_cmd('pr_steps.py')} sweep --dir {shlex.quote(os.path.abspath(a.dir))} --stage {a.stage} --name {_sweep_name(a)}"


def _sweep_name(a):
    return getattr(a, "feature", None) or a.stage


def cleanup_active(a):
    return not getattr(a, "dry_run", False) and derived(a) and is_git(a)


def local_merge_active(a):
    """local-merge runs: derived work branch; independent of `"pr"` (the script itself skips a main repo that has an `origin`)."""
    return not getattr(a, "dry_run", False) and derived(a) and is_git(a)


def cmd(a, name, what):
    """Script command line for subcommand `what` (open|push|ready|cleanup|sweep|local-merge) of pr_steps.py."""
    return f"{tool_cmd('pr_steps.py')} {what} --dir {shlex.quote(os.path.abspath(a.dir))} --stage {a.stage} --name {name}"


def branch(a, name):
    return work_branch(project(a.dir), name)
