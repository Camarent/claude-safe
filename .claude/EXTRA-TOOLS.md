# Extra tools (installed on top of the container image)

Owner rule (2026-10-24). Every tool beyond the container's built-in set is listed here. Process: `practices/audits.md` ("Extra tools").
Tools live in the running container; the `tools-check` step runs `python3 -I tools/xtools.py check --install` (no LLM, before the audits): it installs missing ACCEPTED rows at the pinned version only (kinds uv, cargo, npm, apt). A newer version is a new vetting: `xtools.py outdated` lists them, sec-auditor vets, then bump the row. PENDING rows are never installed.
apt rows are installed by the container entrypoint at start (it is the only step with root); the session runs as a non-root user, so an apt row accepted mid-session is installed at the next start (`tools-check` fails until then).
Statuses: ACCEPTED (vetted by sec-auditor, installed), RECOMMEND-PERMANENT (common/useful for all projects: ask the owner to bake it into the container image), REJECTED (vetted, not installed; reason recorded).
Install location: outside every project tree (`uv tool install`, `cargo install --locked`, release binaries in PATH). Python tool bins: /root/.local/bin (not on PATH: call by full path). Rust tool bins: /opt/cargo/bin.

## Accepted
| Tool | Version | Why | Install | Licence | Network use | Vetted |
|---|---|---|---|---|---|---|
| coverage | 7.17.3 | Python coverage | `uv tool install coverage` | Apache-2.0 | none | ACCEPT 2026-11-16 (owner bump from 7.17.2, no sec-auditor run) |
| vulture | 2.16 | Python dead code | `uv tool install vulture` | MIT | none | ACCEPT 2026-10-24 |
| lizard | 1.26.1 | function length / CCN, Python+JS | `uv tool install lizard` | MIT | none | ACCEPT 2026-11-16 (owner bump from 1.26.0, no sec-auditor run) |
| pip-audit | 2.10.1 | Python dependency CVEs | `uv tool install pip-audit` | Apache-2.0 | advisory DB | ACCEPT 2026-10-24 |
| bandit | 1.9.4 | Python security lint | `uv tool install bandit` | Apache-2.0 | none | ACCEPT 2026-10-24 |
| ruff | 0.17.5 | Python lint + format | `uv tool install ruff` | MIT | none | ACCEPT 2026-11-16 (owner bump from 0.17.4, no sec-auditor run) |
| pytest | 9.2.0 | Python tests (with pytest-cov 7.1.0, MIT, same install) | `uv tool install pytest --with pytest-cov` | MIT | none | ACCEPT 2026-11-05 (owner, no sec-auditor run; baked into base) |
| cargo-audit | 0.22.2 | Rust advisories | `cargo install cargo-audit --locked` | Apache-2.0/MIT | advisory DB | ACCEPT 2026-10-24 |
| cargo-deny | 0.20.2 | Rust licences, bans, advisories | `cargo install cargo-deny --locked` | Apache-2.0/MIT | advisory DB | ACCEPT 2026-10-24 |
| cargo-machete | 0.9.2 | Rust unused dependencies | `cargo install cargo-machete@0.9.2 --locked` | MIT (no build.rs) | none | ACCEPT 2026-10-24 |
| cargo-bloat | 0.12.1 | Rust binary size per function/crate | `cargo install cargo-bloat@0.12.1 --locked` | MIT (no build.rs) | none | ACCEPT 2026-10-24 |
| cargo-llvm-cov | 0.9.1 | Rust coverage (also needs `rustup component add llvm-tools-preview`) | `cargo install cargo-llvm-cov@0.9.1 --locked` | Apache-2.0 OR MIT (no build.rs) | none | ACCEPT 2026-10-24 |
| knip | 6.45.1 | JS dead code, exports, deps (in /opt/jstools) | `npm i --ignore-scripts knip@6.45.1` | ISC | none | ACCEPT 2026-11-11 (owner bump from 6.44.1, no sec-auditor run) |
| jscpd | 5.4.3 | JS duplicate code (in /opt/jstools) | `npm i --ignore-scripts jscpd@5.4.3` | MIT | none | ACCEPT 2026-11-11 (owner bump from 5.4.2, no sec-auditor run) |
| madge | 8.0.0 | JS import cycles (in /opt/jstools) | `npm i --ignore-scripts madge@8.0.0` | MIT | none | ACCEPT 2026-10-24 |
| eslint | 10.15.0 | JS lint with thresholds, flat config (in /opt/jstools) | `npm i --ignore-scripts eslint@10.15.0` | MIT | none | ACCEPT 2026-11-16 (owner bump from 10.14.0, no sec-auditor run) |
| clangd | 1:19.0-63 | C++ language server (nav diag) | `apt-get install -y clangd=1:19.0-63` | Apache-2.0 WITH LLVM-exception | none | ACCEPT 2026-10-29 (owner, no sec-auditor run; baked into the cpp profile) |
| clang-tidy | 1:19.0-63 | C++ lint | `apt-get install -y clang-tidy=1:19.0-63` | Apache-2.0 WITH LLVM-exception | none | ACCEPT 2026-10-29 (owner, no sec-auditor run; baked into the cpp profile) |
| cppcheck | 2.17.1-2 | C++ static analysis | `apt-get install -y cppcheck=2.17.1-2` | GPL-3.0 | none | ACCEPT 2026-10-29 (owner, no sec-auditor run; baked into the cpp profile) |
| mypy | 2.4.1 | Python types (kata) | `uv tool install mypy==2.4.1` | MIT | none | ACCEPT 2026-11-11 (owner, no sec-auditor run; baked into base) |
| huggingface_hub[cli] | 2.4.0 | `hf` model downloads | `uv tool install "huggingface_hub[cli]==2.4.0"` | Apache-2.0 | HF Hub | ACCEPT 2026-11-11 (owner, no sec-auditor run; baked into base) |
| esbuild | 0.28.2 | bundle TS clients | `npm i -g esbuild@0.28.2` | MIT | none | ACCEPT 2026-11-11 (owner, no sec-auditor run; baked into base) |
| web-ext | 10.8.0 | run/lint Firefox extension | `npm i -g web-ext@10.8.0` | MPL-2.0 | none | ACCEPT 2026-11-11 (owner, no sec-auditor run; baked into base) |
| @vscode/vsce | 4.1.1 | package VS Code extension | `npm i -g @vscode/vsce@4.1.1` | MIT | none | ACCEPT 2026-11-16 (owner bump from 4.1.0, no sec-auditor run; baked into base) |
| ninja-build | distro | C++ builds | apt | Apache-2.0 | none | ACCEPT 2026-11-11 (owner; baked into the cpp profile) |
| Firefox ESR 153.7.0, geckodriver 0.37.1, selenium 4.52.0, @vscode/test-electron 3.1.0 | pinned | extension tests under Xvfb | profiles/exttest.Dockerfile | MPL-2.0 / MPL-2.0 / Apache-2.0 / MIT | VS Code download on first test | ACCEPT 2026-11-11 (owner; baked into the exttest profile) |

Vetted by sec-auditor 2026-10-24 (installed before the rule existed; report: /workspace/.work/fr-numbers/l23/tool-vetting.md). Notes: vulture needs a whitelist file per project; lizard has one maintainer but is long-lived and popular (classifier says "Freeware", licence field says MIT); `cargo audit --version` prints "cargo-audit-audit 0.22.2", a display quirk. No telemetry found; network use is advisory-DB fetches only.

## Recommended for permanent install (owner decides; the sec-auditor rated these common and useful for every project)
- Baked into the image (2026-10-29): coverage, pip-audit, bandit, vulture, lizard, ruff, pytest+pytest-cov (base, /usr/local/bin); knip, jscpd, madge, eslint (base, /opt/jstools); cargo-audit, cargo-deny, cargo-machete, cargo-llvm-cov, cargo-bloat (rust profile). Bumping a row also means bumping the ARG in the Dockerfile.

## Rejected
(none yet)
