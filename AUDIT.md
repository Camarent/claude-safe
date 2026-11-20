# AUDIT.md: the pipeline repo itself

Scope: `.claude/tools/` (Python, stdlib only at runtime). Norms: `.claude/practices/audits.md`. Dev setup and commands: `.claude/practices/pipeline-dev.md`.

## Tools

Quiet run (container, tools on PATH): `python3 -I .claude/tools/audit/audit_run.py .`
On the host put the repo-local venv first: `PATH=$PWD/.uv/venv/bin:$PATH python3 -I .claude/tools/audit/audit_run.py .`

```audit-tools
vulture | . | vulture .claude/tools --min-confidence 80 | vulture
bandit | . | bandit -q -r .claude/tools -x .claude/tools/tests -s B404,B603,B606,B607 -f json | bandit
lizard | . | lizard .claude/tools -l python -C 15 -x "*/tests/*" -w | lizard
```

Not parsed by `audit_run` (run them with `scripts/dev.sh`): `check` (ruff lint + format, mypy), `cov` (pytest with branch coverage), `itest` (real-project settings, opt in). Secrets: `gitleaks detect --source . --no-banner --redact` (container image).

## Thresholds and exclusions

- ruff: rules E4 E7 E9 F B UP SIM I C4 RUF100 RUF005 ISC PLE RUF059 RUF001 PLW2901 PLW1510, line length 160 (see `pyproject.toml`); zero findings. Widen one rule family per commit.
- mypy: lenient (untyped bodies unchecked), zero errors.
- bandit: B404, B603, B606, B607 are skipped on purpose (the tools drive git, node and lp); every other finding is fixed or carries `# nosec Bxxx  # reason`.
- vulture: zero at confidence 80.
- Coverage baseline (2026-10-30): host 91% branch with subprocesses (`scripts/dev.sh cov`, floor `fail_under = 90` in pyproject.toml); container 91% (`coverage` per test file, no subprocess patch, nothing skipped). Entry wrappers such as plan.py and wave.py are 0% by design (6 lines). Raise the floor when coverage rises.
- Complexity: `scripts/dev.sh ccn` (lizard -C 15) fails on any function over CCN 15 and lists it; run by release.sh. Fix the function; there is no baseline.
- Licences: runtime has no third-party dependencies; dev tools are listed in `pyproject.toml` and locked in `uv.lock`.
