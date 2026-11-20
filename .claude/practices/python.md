# Python: size and complexity norms (audit)
official = tool default from its documentation; heuristic = our value, no norm. Defaults below are quoted from memory of the tool docs (pylint, ruff, lizard, PEP 8), not re-opened in this pass: check the link before you cite one as final.

## Thresholds
| Metric | Source norm | Recommend | Must split | Source |
|---|---|---|---|---|
| Lines per function | pylint `max-statements` = 50 (official, statements not lines) | >50 lines | >100 lines | https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/too-many-statements.html |
| Cyclomatic complexity | ruff `C901` `max-complexity` = 10 (official); lizard `-C` default 15 | >10 | >15 (the ratchet in `scripts/dev.sh ccn`) | https://docs.astral.sh/ruff/settings/#lint_mccabe_max-complexity ; https://github.com/terryyin/lizard |
| Function parameters | pylint `max-args` = 5 (official) | >4 | >7 (heuristic, as clippy `too_many_arguments`) | https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/too-many-arguments.html |
| Branches | pylint `max-branches` = 12 (official) | >8 | >12 | pylint messages `too-many-branches` |
| Returns | pylint `max-returns` = 6 (official) | >4 | >6 | pylint messages `too-many-return-statements` |
| Local variables | pylint `max-locals` = 15 (official) | >10 | >15 | pylint messages `too-many-locals` |
| Lines per file | pylint `max-module-lines` = 1000 (official) | >300 | >500 (our limit, from js.md) | pylint messages `too-many-lines` |
| Line length | PEP 8 = 79 (official); this repo = 160 (ruff, pyproject) | repo value | repo value | https://peps.python.org/pep-0008/ |
| Nesting depth | none in the standard tools | >3 | >4 (ESLint max-depth, js.md) | heuristic |

## Tools (run through `scripts/dev.sh`, host venv under `.uv`)
| Goal | Command | Note |
|---|---|---|
| Lint, format, types | `scripts/dev.sh check` | ruff rule families are widened one per cleanup commit (pyproject) |
| Complexity | `scripts/dev.sh ccn` | limit: no function over CCN 15 (lizard lists any that is) |
| Dead code | `scripts/dev.sh dead` | vulture, confidence 80 |
| Security | `scripts/dev.sh sec` | bandit; each `nosec` carries its reason |
| Coverage | `scripts/dev.sh cov` | floor `fail_under` in pyproject; raise it when coverage rises |

## Rules
- A new function over the "recommend" column needs a reason in review; over "must split" it does not merge.
- No literals for paths, branch names, timeouts or tool lists in pipeline code: use `pipeline.configuration`.
- Tests remove every temp dir, file, process and env change they create.
