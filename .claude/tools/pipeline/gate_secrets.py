"""Secret scan of the files a task changed: gitleaks on a temp copy of those files (changed vs base, untracked not ignored), so the scan
never walks ignored data. `scan_diff` never raises. Gitleaks missing is a note on stderr and no finding; a failed scan is a finding,
so a broken scanner blocks instead of passing silently. A finding is `file:rule`, never the secret."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from .configuration import (
    GITLEAKS_CLEAN_RC,
    GITLEAKS_LEAKS_RC,
    GITLEAKS_TOOL,
    SECRET_SCAN_CONFIG_KEY,
    SECRET_SCAN_TIMEOUT_S,
)
from .gitx import try_run

REPORT_NAME = "report.json"
SRC_NAME = "src"


def _note(msg):
    print(f"secret scan: {msg}", file=sys.stderr, flush=True)


def _changed_files(cwd, base):
    """Paths (relative to cwd, the repo root) changed vs base, plus untracked not-ignored files -> sorted list, or None when git fails."""
    names: set[str] = set()
    for args in (("diff", "--name-only", "--diff-filter=d", base), ("ls-files", "--others", "--exclude-standard")):
        r = try_run(cwd, *args)
        if r is None or r.returncode != 0:
            return None
        names.update(n for n in r.stdout.splitlines() if n)
    return sorted(names)


def _copy_files(cwd, names, dest):
    """Copy the regular files of names (not symlinks, not deleted) under dest, keeping their relative paths."""
    for name in names:
        src = os.path.join(cwd, name)
        if os.path.islink(src) or not os.path.isfile(src):
            continue
        target = os.path.join(dest, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(src, target)


def _findings(report, src):
    """gitleaks JSON report -> `file:rule` lines (file relative to the copy root); the secret itself is never read out."""
    with open(report, encoding="utf-8") as f:
        items = json.load(f) or []
    return [f"{os.path.relpath(i.get('File', ''), src)}:{i.get('RuleID', '')}" for i in items]


def _scan(cwd, base, cfg):
    names = _changed_files(cwd, base)
    if names is None:
        raise OSError(f"git could not list the files changed vs {base}")
    tmp = tempfile.mkdtemp(prefix="secret-scan-")
    try:
        src = os.path.join(tmp, SRC_NAME)
        os.mkdir(src)
        _copy_files(cwd, names, src)
        report = os.path.join(tmp, REPORT_NAME)
        argv = [GITLEAKS_TOOL, "detect", "--no-git", "--source", src, "--redact", "--no-banner", "--report-format", "json", "--report-path", report]
        config = (cfg or {}).get(SECRET_SCAN_CONFIG_KEY)
        if config:
            argv += ["--config", os.path.join(cwd, config)]
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=SECRET_SCAN_TIMEOUT_S, check=False)
        if r.returncode == GITLEAKS_CLEAN_RC:
            return []
        if r.returncode == GITLEAKS_LEAKS_RC:
            return _findings(report, src)
        return [f"gitleaks exit {r.returncode}: scan failed"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scan_diff(cwd, base, cfg=None):
    """Secret scan of the files changed vs base in the repo cwd -> `file:rule` lines: [] when clean or gitleaks is missing (a note on stderr).
    cfg: the project settings (pipeline.configuration.project); its secret_scan_config path, relative to cwd, selects the gitleaks rules."""
    if shutil.which(GITLEAKS_TOOL) is None:
        _note(f"{GITLEAKS_TOOL} not installed, skipped")
        return []
    try:
        return _scan(cwd, base, cfg)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return [f"secret scan error: {type(e).__name__}"]
