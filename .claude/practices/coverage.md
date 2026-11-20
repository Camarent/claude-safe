# Test coverage norms (all projects)

Default source: G = https://testing.googleblog.com/2020/08/code-coverage-best-practices.html

## 1. Thresholds and meaning
- Google guide: 60% acceptable, 75% commendable, 90% exemplary; no single ideal, it depends on criticality, change frequency, code lifetime (G).
- Do not chase 100%: false safety, low-value tests; above ~90% returns are logarithmic (G).
- Coverage = "line executed", not "verified"; the value is in what is NOT covered: look at uncovered lines in review (G).
- Core and critical seams (migrations, parsers, scheduler) stay above average; the level follows criticality (G). For the core use 90% ("exemplary" in G) as the guide.
- line = line executed; branch = each condition branch taken; function = function called (Node docs https://nodejs.org/api/test.html, lines/branches/functions thresholds).

## 2. Delta
- G: a project-wide threshold >90% is hardly worth it; 99% per commit is reasonable, 90% is the lower bound; new code must pass the threshold; a coverage drop that breaks the gate must not reach main.
- Patch coverage (changed lines only) is separate from project coverage (all code, against the PR base): https://docs.codecov.com/docs/commit-status
- Rule: gate on patch >= 90% (G); a drop in overall coverage is a signal to investigate, not an automatic rejection (G: delta gating is acceptable).

## 3. Tools (free)
- Rust: `cargo +stable install cargo-llvm-cov --locked` (needs rustc 1.87+), then `cargo llvm-cov --workspace` (summary), `--lcov --output-path lcov.info`, `--html`, `--fail-under-lines 80`, `--ignore-filename-regex '(generated|vendor|vectors)'`. https://github.com/taiki-e/cargo-llvm-cov
  - `rustup component add llvm-tools-preview` may be needed (README only names the component and env CARGO_LLVM_COV_SETUP).
  - Branches: `--branch`, unstable, nightly only (README).
  - Exclude code in source: `#[cfg_attr(coverage_nightly, coverage(off))]` (nightly) (README).
- Alternative: `cargo install --locked cargo-tarpaulin`; `cargo tarpaulin --out Lcov --exclude-files 'gen/*' --fail-under 80`; on Linux the default ptrace works on x86_64 only, otherwise `--engine llvm`. https://github.com/xd009642/tarpaulin
- Rust -> WASM: measure the native core on the host as usual. `#[wasm_bindgen_test]` tests on wasm32-unknown-unknown can be measured but experimentally: nightly, Rust >= 1.87, wasm-bindgen-test >= 0.3.57, RUSTFLAGS `-Cinstrument-coverage -Zno-profiler-runtime`, `cargo +nightly llvm-cov test --target wasm32-unknown-unknown` (full command in the source); may break. https://wasm-bindgen.github.io/wasm-bindgen/wasm-bindgen-test/coverage.html JS glue coverage is measured separately with JS tools.
- JS: `node --test --experimental-test-coverage` (flag is Experimental; summary in spec/tap reporter; lcov: `--test-reporter=lcov --test-reporter-destination=lcov.info` next to another reporter). https://nodejs.org/api/test.html
  - Thresholds `--test-coverage-lines=80 --test-coverage-branches=70 --test-coverage-functions=90` (since v22.8.0, exit 1); `--test-coverage-include/exclude` globs (since v22.5.0, Experimental). https://nodejs.org/api/cli.html
  - Exclusions: node_modules and the tests themselves are excluded by default; `--test-coverage-exclude='src/generated/**'`; in code `/* node:coverage disable */ ... /* node:coverage enable */`, `/* node:coverage ignore next N */`.
- c8 (V8 coverage for any run): `c8 --check-coverage --lines 90 --branches 80 npm test`; `-x/--exclude`, `--per-file`, `-r html -r lcov`, config `.c8rc.json`. https://github.com/bcoe/c8
- Always exclude generated, vendored, test vectors/fixtures, or the percent is skewed.

## 4. Test quality beyond percent
- Mutation testing (G recommends it as the best check that tests really verify). Rare and selective, critical modules only, not in every CI.
- Rust: `cargo install --locked cargo-mutants`; `cargo mutants --file src/core.rs`; `cargo mutants --in-diff changes.diff` mutates changed lines only. Outcomes: caught / missed / unviable; missed = a hole in the tests. https://mutants.rs/ , https://mutants.rs/in-diff.html Needs non-flaky tests.
- JS: `npm init stryker@latest`, then `npx stryker run` (`mutate` option and thresholds in the config). https://stryker-mutator.io/docs/stryker-js/getting-started/
- Target mutation score: no source norm.
