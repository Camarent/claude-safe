# Rust: size and complexity norms (audit)
official = primary source opened; sec = forum/secondary; heuristic = our value, no norm.

## Thresholds
| Metric | Source norm | Recommend | Must split | Source |
|---|---|---|---|---|
| Lines per function | clippy `too_many_lines` = 100 (official) | >50 | >100 | https://doc.rust-lang.org/clippy/lint_configuration.html |
| Cognitive complexity | clippy `cognitive_complexity` = 25 (official) | >15 (Sonar S3776 = 15, sec) | >25 | same; https://community.sonarsource.com/t/s3776-reason-for-the-current-default-value-of-15/127103 |
| Function parameters | clippy `too_many_arguments` = 7 (official) | >4 | >7 | clippy lint_configuration |
| bool parameters | `max_fn_params_bools` = 3 (official) | >1 | >3 | clippy lint_configuration |
| bool struct fields | `max_struct_bools` = 3 (official) | >2 | >3 | clippy lint_configuration |
| Type complexity | `type_complexity` = 250 (official) | add an alias at >100 | >250 | clippy lint_configuration |
| Block nesting | `excessive_nesting`: default 0 = off (official) | >3 | >4 (ESLint max-depth=4) | clippy lint_configuration |
| Lines per file/module | no norm (Book, API Guidelines, Perf Book give none) | >400 | >800 | heuristic |
| Cyclomatic | not in clippy | >10 | >15 | heuristic |
| Enum variant | `enum_variant_size_threshold` = 200 bytes (official) | >128 | >200 (Box) | clippy lint_configuration |
| Result Err type | `large_error_threshold` = 128 bytes (official) | >64 | >128 (Box) | clippy lint_configuration |
| Stack array | `array_size_threshold` = 16384; `too_large_for_stack` = 200 (official) | -- | -- | clippy lint_configuration |

## Tools (run from the crate/workspace root)
| Goal | Command | Note |
|---|---|---|
| Size/complexity | `cargo clippy --workspace --all-targets -- -D warnings -W clippy::too_many_lines -W clippy::cognitive_complexity -W clippy::too_many_arguments -W clippy::type_complexity -W clippy::excessive_nesting -W clippy::fn_params_excessive_bools -W clippy::struct_excessive_bools` | thresholds in `clippy.toml`: `too-many-lines-threshold = 50` etc. |
| Panics | `cargo clippy -- -W clippy::unwrap_used -W clippy::expect_used -W clippy::indexing_slicing -W clippy::panic` | restriction group, enable explicitly |
| Vulnerabilities | `cargo install cargo-audit; cargo audit` | RustSec DB |
| Licences/bans/duplicates | `cargo deny check` (advisories bans licenses sources) | needs `deny.toml` (`cargo deny init`) |
| Unused deps (exact) | `cargo +nightly udeps --all-targets` | needs nightly (official README) |
| Unused deps (fast) | `cargo machete --with-metadata` | exit 1 = found; imprecise (official README) |
| Binary size | `cargo bloat --release -n 20` ; `cargo bloat --release --crates` | estimates; no WASM support (official README) |
| Code duplication | `jscpd` as in `js.md` | understands Rust |

## Not found
- No numeric norms in the Rust Book, Rust API Guidelines or Rust Performance Book (file/function size): not all chapters opened.
- Lint groups (nursery/pedantic/restriction) not checked; hence the explicit `-W` flags.
- Sonar Rust rules: rules.sonarsource.com unreachable from the environment.
