# TypeScript guidelines

Digest of the TypeScript handbook and the typescript-eslint `strictTypeChecked` + `stylisticTypeChecked` presets. Read `js.md` too: everything there applies. The project's own CLAUDE.md and tsconfig win.

## Compiler settings (assume on, ask before turning off)
`strict: true` plus `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`, `noFallthroughCasesInSwitch`, `verbatimModuleSyntax`. Fix the type; do not weaken the config.

## Types
- No `any`. Use `unknown` for untrusted data and narrow it. Narrow with `typeof`, `in`, discriminant fields and user-defined guards; parse external data with a schema (zod/valibot or hand-written) instead of casting.
- No `as` assertions to silence errors, no `!` non-null assertions without a one-line reason. `as const` and `satisfies` are fine.
- Model states as discriminated unions (`{ kind: "ok"; value: T } | { kind: "err"; error: E }`) and check exhaustiveness with a `never` default. Prefer unions of string literals over `enum` (avoid `const enum`).
- `interface` for object shapes that may be extended or implemented, `type` for unions, tuples, mapped and conditional types. Be consistent with the codebase.
- Name generic parameters for meaning when there are several (`TKey`, `TValue`); constrain them (`extends`). Do not use a generic that appears once in a signature.
- Brand ids and units when mixing them up would be a bug (`type UserId = string & { readonly __brand: "UserId" }`).
- Optional property vs `| undefined`: choose one meaning and keep it. Prefer `readonly` and `ReadonlyArray` for data you do not mutate.

## Functions and modules
- Annotate exported function parameters and return types; let inference work for locals. Do not annotate what the compiler already knows.
- `import type` for types. No `namespace`, no `export =`.
- Do not use `Function`, `Object`, `{}` and wrapper types such as `String` as types; write the signature.
- Errors: `catch (e)` is `unknown`; narrow before use. Throw `Error` subclasses, not strings. Return `Result`-like unions where failure is expected.

## DRY and SOLID in TypeScript
Base rules and `js.md` apply. Type-level forms:
- Discriminated union plus exhaustive `switch`: `default: { const _never: never = x; throw new Error(...) }`, or lint `switch-exhaustiveness-check`. A new variant then fails to compile at every unhandled site.
- Open/closed: a typed table (`const handlers = { a, b } satisfies Record<Kind, Handler>`) fails to compile when a `Kind` has no entry.
- Define a small interface in the consumer's module, with only the methods it calls (`type Clock = { now(): number }`). The implementation satisfies it structurally; no `implements` needed.
- Dependency injection: pass `fetch`, clock or storage as typed parameters (`typeof fetch`, or a narrow type), not a DI container.
- Derive types from one source: `typeof table[number]`, `keyof typeof`, `z.infer`, `ReturnType`. Do not write the same shape twice.
- One module for constants (`as const`), and a type derived from it, not a parallel union.

## Tests in TypeScript
Base rules and `js.md` "Tests (TDD)" apply. One test run: `vitest run path -t "name"` or `jest path -t "name"`.
- Tests are type-checked: `tsc --noEmit` covers them. No `any` in a fake; type it with the consumer's small interface.
- Build fakes with `satisfies Clock` so a changed interface breaks the test at compile time.
- Do not cast a partial object to the full type (`{} as Foo`) to pass a test. Build a real value with a typed factory (`makeUser(overrides?: Partial<User>)`).
- Typed boundary tests: pass `unknown` or invalid input to the parser and assert it rejects. `// @ts-expect-error` with a reason is allowed to test a runtime guard.
- Mock only boundaries. `vi.mocked(fn)` or `jest.mocked(fn)` keeps stubs typed.

## Tooling (must be clean)
`tsc --noEmit`, ESLint with `typescript-eslint` with `strictTypeChecked` + `stylisticTypeChecked`, the project's formatter. Those presets cover `no-floating-promises`, `no-misused-promises`, `no-explicit-any` and `no-unsafe-*`. Enable `switch-exhaustiveness-check` and `consistent-type-imports` by hand. A `// @ts-expect-error` needs a reason; never `@ts-ignore`.

## Check in review
`any`, `as` or `!` hiding a real case, a union not exhaustively handled, a type that allows an invalid state, an exported function without a return type, a cast of parsed JSON, an enum where a literal union would do.
- A `switch` on a type tag (`kind`, `type`) without a `never` check or the exhaustiveness lint.
- The same shape or rule typed or written in two places instead of derived.
- A `{} as Foo` stub in a test.
