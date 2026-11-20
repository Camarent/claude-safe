# TypeScript guidelines

Digest of the TypeScript handbook and the typescript-eslint `strict` + `stylistic` configs. Read `js.md` too: everything there applies. The project's own CLAUDE.md and tsconfig win.

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
- `import type` for types. No `namespace`, no `export =`. Avoid default exports and barrel files that create import cycles.
- Do not use `Function`, `Object`, `{}` or `object` as types; write the signature.
- Errors: `catch (e)` is `unknown`; narrow before use. Throw `Error` subclasses, not strings. Return `Result`-like unions where failure is expected.

## Tooling (must be clean)
`tsc --noEmit`, ESLint with `typescript-eslint` strict-type-checked, the project's formatter. Rules to keep on: `no-floating-promises`, `no-misused-promises`, `no-explicit-any`, `no-unsafe-*`, `switch-exhaustiveness-check`, `consistent-type-imports`. A `// @ts-expect-error` needs a reason; never `@ts-ignore`.

## Check in review
`any`, `as` or `!` hiding a real case, a union not exhaustively handled, a type that allows an invalid state, an exported function without a return type, a cast of parsed JSON, an enum where a literal union would do.
