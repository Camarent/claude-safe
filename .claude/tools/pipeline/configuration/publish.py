"""Validation of the `publish` setting (artifact republish entries)."""

import os

from .errors import ConfigError
from .layout import PUBLISH_BUILD, PUBLISH_CWD, PUBLISH_FILES, PUBLISH_KEY, PUBLISH_NO_CHAIN, PUBLISH_PAGE


def _escapes_project(path):
    """True for an absolute path or one that climbs out with `..`."""
    return os.path.isabs(path) or ".." in os.path.normpath(path).split(os.sep)


def _check_publish_entry(e, files, bad):
    """Raise ConfigError BAD for a chained build, a malformed files map, or a cwd / published path that leaves the project."""
    if PUBLISH_NO_CHAIN in e[PUBLISH_BUILD] or not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
        raise ConfigError(bad)
    if _escapes_project(str(e.get(PUBLISH_CWD) or ".")) or any(_escapes_project(k) for k in files):
        raise ConfigError("pipeline.json: `publish` `cwd` and `files` keys must be relative paths inside the project (no leading / and no ..)")


def publish_entries(cfg):
    """-> {url: {"cwd", "build", "page", "files"}} from cfg `publish` (validated; {} when absent). `cwd` defaults to ".", `files` to {}."""
    raw = cfg.get(PUBLISH_KEY) or {}
    bad = 'pipeline.json: `publish` must map an artifact URL to {"build": "<one command>", "page": "<file>", "cwd": "<dir>", "files": {"<published path>": "<file>"}}'
    if not isinstance(raw, dict):
        raise ConfigError(bad)
    out = {}
    for url, e in raw.items():
        if not isinstance(e, dict) or not isinstance(e.get(PUBLISH_BUILD), str) or not isinstance(e.get(PUBLISH_PAGE), str):
            raise ConfigError(bad)
        files = e.get(PUBLISH_FILES) or {}
        _check_publish_entry(e, files, bad)
        out[url] = {
            PUBLISH_CWD: str(e.get(PUBLISH_CWD) or "."),
            PUBLISH_BUILD: e[PUBLISH_BUILD],
            PUBLISH_PAGE: e[PUBLISH_PAGE],
            PUBLISH_FILES: dict(files),
        }
    return out
