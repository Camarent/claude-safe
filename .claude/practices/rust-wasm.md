# Rust -> WASM: size and the JS<->WASM boundary (audit)
Complements rust.md (code thresholds). official = primary source; heuristic = our value, no norm.

## Thresholds
| Metric | Source norm | Recommend | Must split/fix | Source |
|---|---|---|---|---|
| .wasm size (before gzip) | no norm | <512 KB | >1 MB: twiggy analysis | heuristic |
| opt-level | `'s'` or `'z'`, "Always measure!" (official) | compare s and z | -- | same |
| LTO | `lto = true` (official) | enable | -- | same |
| Allocator | default ~10 KB (official) | -- | -- | same |
| Data across the boundary | no number; serde-wasm-bindgen: less code and faster than JSON (official, no figures) | <=1 call per frame/action | a per-item call in a loop | https://docs.rs/serde-wasm-bindgen/latest/serde_wasm_bindgen/ |
| Function size/nesting | see rust.md | -- | -- | -- |

## Boundary rules (source; confidence)
- `format!`/`to_string` bloat code; replace with static strings (official, code-size.html).
- Panics hide in indexing, division, unwrap: use `.get(i)`, `checked_div`, handle Err (official, code-size.html).
- On wasm32-unknown-unknown a panic is a trap/abort anyway (official, code-size.html).
- User data (import/save/JSON from JS): no `unwrap`/`expect`/indexing; return `Result<T, JsError>`; Derived from the points above. Check: clippy `unwrap_used`, `expect_used`, `indexing_slicing`.
- Serialization: `serde-wasm-bindgen` over JSON strings ("officially preferred", official); by default Map -> JS `Map`, for plain objects use `Serializer::json_compatible()`.
- Copying: every string/Vec crossing the boundary is copied; hand large buffers over as `Uint8Array`/view and do not ship them back and forth.
- Context: the rustwasm/book project is marked "no longer maintained" (official); check that its advice is current.

## Tools
| Goal | Command | Note |
|---|---|---|
| Build | `wasm-pack build --release --target web` | wasm-pack installs wasm-opt |
| What takes space | `twiggy top -n 20 pkg/<name>_bg.wasm` ; `twiggy dominators`, `twiggy paths` | official (top) |
| Size per crate | cargo-bloat has no WASM support ("Use twiggy instead", official README); for native builds `cargo bloat --release --crates` | |
| Cut panic code | `wasm-snip` + `wasm-opt --dce` | official, see wasm-snip docs for arguments |
| LLVM IR | `cargo rustc --release -- --emit llvm-ir` | official |
| The rest | cargo clippy/audit/deny/udeps/machete: commands in rust.md; for wasm: `cargo clippy --target wasm32-unknown-unknown -- -D warnings` | |

## Not found
- A numeric norm for .wasm size; the numbers above are heuristics.
- Official figures for the cost of copying across the boundary; the wasm-bindgen guide (exported-rust-types) does not cover it.
- Confirmation of `panic = "abort"` as a recommendation on the opened code-size page.
