# Rust guidelines

Digest of the Rust API Guidelines (rust-lang.github.io/api-guidelines) and the Clippy defaults. Gates are in `practices/rust.md` and `practices/rust-wasm.md`. The project's own CLAUDE.md wins.

## Ownership and types
- Borrow by default: take `&str`, `&[T]`, `&Path` rather than `String`, `Vec<T>`, `PathBuf` in parameters; return owned values. Clone only with a reason, never to silence the borrow checker.
- Make invalid states unrepresentable: enums over flag fields, newtypes for units and ids, `Option` instead of sentinel values. Builders for constructors with many options.
- Accept generics or `impl Trait` at the edge (`impl AsRef<Path>`, `impl Into<String>`); return concrete types. `dyn Trait` only where you need a runtime choice.
- Derive the common traits when they make sense: `Debug` always, `Clone`, `PartialEq`, `Eq`, `Hash`, `Default` [C-COMMON-TRAITS]. Implement `From`, `FromStr`, `Display` rather than ad hoc `to_x` helpers [C-CONV-TRAITS].

## Errors
- Library code returns `Result` with a typed error (`thiserror` or a hand-written enum) that implements `std::error::Error`, `Debug`, `Display`; applications may use `anyhow`.
- No `unwrap()` or `expect()` outside tests, examples and provable invariants; an `expect` message states the invariant. `panic!` for bugs, never for expected failures.
- `?` for propagation; add context where the error crosses a layer. Do not swallow errors with `let _ =` on fallible calls.

## Naming and API shape (API guidelines)
- `snake_case` functions and modules, `CamelCase` types, `SCREAMING_SNAKE_CASE` constants. Getters have no `get_` prefix. Conversions: `as_` (cheap borrow), `to_` (expensive), `into_` (consumes) [C-CONV].
- Iterators: implement `IntoIterator` for collections, name methods `iter`, `iter_mut`, `into_iter` [C-ITER].
- Public items are documented with an example that compiles (doc test); `# Errors`, `# Panics`, `# Safety` sections where they apply [C-EXAMPLE, C-FAILURE].
- Keep the public surface small: `pub(crate)` by default. Do not expose dependency types unless intended (semver risk) [C-STABLE].

## Unsafe and concurrency
- `unsafe` blocks are small, each with a `// SAFETY:` comment stating the invariant; wrap them in a safe API. No `unsafe` for convenience or speed without a measurement.
- Prefer message passing or `Arc<Mutex<_>>`/`RwLock` scoped tightly; never hold a `std::sync` lock guard across `.await`; use `tokio::sync::Mutex` if the lock must span one. Mark types `Send`/`Sync` by construction, not with `unsafe impl`.

## Tests (TDD)
Cycle and rules are in base.md. Rust forms:
- Run one test with output: `cargo test <name> -- --nocapture`. `cargo nextest run` is optional and faster, but skips doc tests: run `cargo test --doc` too.
- Unit tests live in `#[cfg(test)] mod tests` beside the code and may reach private items; test through the public function when you can. Integration tests in `tests/` see only the public API.
- Doc tests on public items are tests: keep them compiling and asserting something.
- Make tests return `Result<(), E>` and use `?` instead of `unwrap`. Assert on the error variant with `matches!` or `assert_eq!`. Use `#[should_panic(expected = "text")]` only for code that must panic, and always give `expected`.
- Files: `tempfile::tempdir()` or `NamedTempFile`; they delete on drop. Never write into the repo or `$HOME`.
- Seams: a trait the consumer owns (clock, store, HTTP client) with a small hand-written fake in the test. Pure functions need none.
- `proptest` for parsers, encoders and invariants (round trip, "never panics", sorted output). Keep the seed file it writes for a failing case.
- No sleeps. Async time: `#[tokio::test(start_paused = true)]` or `tokio::time::pause()` (needs tokio feature `test-util`), then `advance`.

## DRY and SOLID (Rust forms)
- Consumer defines the trait, small (one to three methods), at a real seam. 
- Open/closed: a closed set of cases is an `enum` with an exhaustive `match` and no `_` arm, so a new variant breaks the build at every site. Repeated `match` on one enum at many sites: move it into a method on the enum.
- Open set (plugins, user-supplied behaviour): a trait. `dyn Trait` for a runtime-chosen or heterogeneous set; generics (`T: Trait`) when the type is known at compile time.
- Reuse by composition: a struct holding the part, a newtype for a distinct meaning, `Deref` never as fake inheritance. Share behaviour with a trait default method or a free function.
- Conversions once: `From`/`TryFrom` between types, `?` converts errors through `From`. Do not repeat a field-by-field mapping.
- A generic helper with several type parameters used twice is worse than two plain copies.

## Tooling (must be clean)
`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`, `cargo audit`/`cargo deny` for dependencies. Do not `#[allow]` a lint without a one-line reason. Avoid needless `clone`, `collect` into a `Vec` only to iterate, and `String` allocation in hot loops.

## Check in review
`unwrap` in non-test code, `unsafe` without `SAFETY`, public type exposing a dependency, `std::sync` lock guard held across `.await`, `clone` to dodge a borrow, error type that loses the source, missing doc test on a new public function, a rule or constant written in two places, a `match` on one enum repeated at several call sites, a test that cannot fail (no assert, or asserts a value it just set), a hand-made mock of your own pure code, `thread::sleep` or the real clock in a test, `should_panic` without `expected`.
