"""explore: find the files a request needs by letting the local model explore the repos (agent_files with a read-only shell).
The repos are the live main checkouts: the stage's work branch, clean by the pipeline's dirty check, with gitignored content present. Nothing here writes;
the `bash` tool is confined by safeshell (read-only commands only)."""

import os

import agent_files


def explore(repos, request, log=None, post=None, run=None, leads=""):
    """repos {name: live dir} -> [(repo name, path, why, category)] most important first, or None when no repo exists or the loop failed."""
    kw = {}
    if post:
        kw["post"] = post
    if run:
        kw["run"] = run
    live = {n: r for n, r in repos.items() if os.path.isdir(r)}
    if not live:
        return None
    agent_files.BOXES.clear()
    agent_files.BOXES.update(live)
    try:
        return agent_files.agent_files(live, request, log=log, arch=agent_files.arch_map(live), leads=leads, **kw)
    finally:
        agent_files.BOXES.clear()
