# Contributing

Thanks for helping. This page covers how to set up, check and submit a change.

## Dev setup

Run `scripts/dev.sh sync` once. It installs the Python environment the tools need.

## Checks

Run the checks before you open a pull request:

- `scripts/dev.sh check` for lint and the main checks.
- `scripts/dev.sh cov` for coverage.
- `scripts/dev.sh dead` for dead code.
- `scripts/dev.sh sec` for security scans.
- `scripts/dev.sh ccn` for complexity.

Run a single test file with `scripts/dev.sh test <file>`.

## Branches and commits

Work on a short-lived branch. Only `master` is long-lived. Land a branch with a
squash merge, so `master` keeps one commit per change. Write each commit subject
as a single line: a type, an area and what changed.

## Docs and tests

Docs and tests must stay in step with the code. A change that alters behaviour
updates the matching doc and test in the same pull request. Tests clean up after
themselves: every temp file, directory, process and environment change is removed
when the test ends.

## Privacy

Do not put personal paths, names or tokens into commits, docs or tests. The privacy
check in CI rejects them. Use placeholders instead.

## License of contributions

By contributing, you agree that your change is released under the MIT license in
`LICENSE`.
