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
- Prefer message passing or `Arc<Mutex<_>>`/`RwLock` scoped tightly; never hold a lock across `.await`. Mark types `Send`/`Sync` by construction, not with `unsafe impl`.

## Tooling (must be clean)
`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`, `cargo audit`/`cargo deny` for dependencies. Do not `#[allow]` a lint without a one-line reason. Avoid needless `clone`, `collect` into a `Vec` only to iterate, and `String` allocation in hot loops.

## Check in review
`unwrap` in non-test code, `unsafe` without `SAFETY`, public type exposing a dependency, lock held over `.await`, `clone` to dodge a borrow, error type that loses the source, missing doc test on a new public function.
