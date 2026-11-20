# Vanilla JS / ES modules + Preact-style UI (no TS): norms and audit
official = primary source; sec = forum/secondary; heuristic = our value, no norm.

## Thresholds
| Metric | Source norm | Recommend | Must split | Source |
|---|---|---|---|---|
| Lines per file | ESLint `max-lines` = 300 (official) | >300 | >500 | https://eslint.org/docs/latest/rules/max-lines |
| Lines per function | ESLint `max-lines-per-function` = 50 (official; skipBlankLines/skipComments = false); Sonar S138 = 200 (sec) | >50 | >100 | https://eslint.org/docs/latest/rules/max-lines-per-function ; https://community.sonarsource.com/t/reduce-number-of-lines-for-some-issues/18474 |
| Cyclomatic | ESLint `complexity` = 20 (official) | >10 | >20 | https://eslint.org/docs/latest/rules/complexity |
| Cognitive | Sonar S3776 = 15 (sec) | >10 | >15 | https://community.sonarsource.com/t/s3776-reason-for-the-current-default-value-of-15/127103 |
| Block nesting | ESLint `max-depth` = 4 (official) | >3 | >4 | https://eslint.org/docs/latest/rules/max-depth |
| Parameters | ESLint `max-params` = 3 (official); Sonar S107 = 7 (sec) | >3 | >7 | https://eslint.org/docs/latest/rules/max-params |
| Preact component (lines) | no norm | >100 | >200 | heuristic |
| Component props | no norm | >5 | >8 | heuristic |
| Import cycles | no norm | 0 | any cycle | heuristic |
| Code duplication | no norm | >3% | >5% or clone >30 lines | heuristic |

## ESLint config (flat, eslint.config.js)
`rules: { 'max-lines': ['warn',{max:300,skipBlankLines:true,skipComments:true}], 'max-lines-per-function': ['warn',{max:50,skipBlankLines:true,skipComments:true}], complexity:['warn',10], 'max-depth':['warn',3], 'max-params':['warn',4], 'max-nested-callbacks':['warn',3] }`. The values are our heuristics.

## Tools (from the root)

Run lint, dead-code, duplication and cycle checks through `audit/audit_run.py <repo> --only eslint|knip|jscpd|madge` (commands come from the repo AUDIT.md), wrapped like any check: `cs-qrun -- cs-audit <repo> --only jscpd`. The binaries live in `/opt/jstools/node_modules/.bin/`; `npx --no-install` cannot see them. Rows below show the raw command for reference.

| Goal | Command | Note |
|---|---|---|
| Lint + thresholds | `/opt/jstools/node_modules/.bin/eslint .` | thresholds in the config above |
| Cognitive complexity | `npx eslint --rule 'sonarjs/cognitive-complexity: [error,15]' .` with eslint-plugin-sonarjs | needs `eslint-plugin-sonarjs` (not installed in `/opt/jstools`) |
| Dead code/exports/deps | `/opt/jstools/node_modules/.bin/knip` | official (getting-started); `--production` exists in `--help` |
| Unused deps | `npx depcheck` | needs network (`npx`), not installed |
| Duplication | `/opt/jstools/node_modules/.bin/jscpd src --min-lines 10 --min-tokens 70 --threshold 5` | flags verified in `--help`; thresholds are heuristics; without `--kind` finds exact only (official README) |
| Import cycles | `/opt/jstools/node_modules/.bin/madge --circular --extensions js,jsx src` | official README |
| Vulnerabilities | `npm audit --omit=dev` ; `npm audit --audit-level=high` | needs network (`npx`), not installed |
| Bundle size | `npx source-map-explorer dist/*.js` (needs sourcemaps) | needs network (`npx`), not installed |

## Not found
- Google JS Style Guide and Airbnb: numeric size/complexity limits not checked (treat as no norm).
- rules.sonarsource.com pages are unreachable from the environment: Sonar JS rule defaults are not confirmed.
- Norms for Preact components: not searched in the Preact docs.
