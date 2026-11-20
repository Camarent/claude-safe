# Python guidelines

Digest of PEP 8, PEP 20, PEP 484/526 typing, and the ruff/mypy rule sets. Gates and tools are in `practices/python.md`. The project's own CLAUDE.md and pyproject win.

## Style
- PEP 8 names: `snake_case` functions and variables, `CapWords` classes, `UPPER_CASE` constants. Let `ruff format` own layout; do not hand-format.
- Explicit is better than implicit; flat is better than nested; errors never pass silently (PEP 20). Early return over deep nesting.
- f-strings for formatting; `pathlib.Path` for paths; `with` for every resource; comprehensions only when they stay readable on one line or two.
- Do not use mutable default arguments (`def f(x=[])`); use `None` and create inside. Do not shadow builtins (`id`, `type`, `list`).
- Stdlib first: `dataclasses`, `enum`, `itertools`, `functools`, `collections`, `argparse`, `subprocess.run([...], check=True)`. No new dependency for a few lines.

## Types
- Annotate public functions and anything crossing a module boundary; keep annotations accurate (`list[str]`, `X | None`, `Sequence`, `Mapping` for inputs; concrete types for returns). `mypy` clean; no blanket `# type: ignore` (give the code and a reason).
- `@dataclass(frozen=True, slots=True)` for plain data; `TypedDict` or a model for parsed JSON; `Protocol` for duck-typed seams instead of inheritance. `Any` only at a real dynamic edge.
- Parse and validate external input once at the boundary (pydantic or explicit checks), then pass typed objects.

## Errors and safety
- Catch the narrowest exception; no bare `except:` and no `except Exception: pass`. Re-raise with `raise ... from err`. Custom exceptions subclass a project base. Use exceptions, not return codes.
- Never build shell or SQL from strings: `subprocess` with an argument list (no `shell=True`), parameterised queries. `yaml.safe_load`, never `pickle.loads` on untrusted data. Use `secrets` for tokens, not `random`.
- No `assert` for runtime validation (stripped with `-O`). `logging` with lazy `%s` arguments, never `print` in library code; do not log secrets.
- Time: timezone-aware `datetime` in UTC; money with `Decimal` or integers.

## Structure and tests
- Small modules, one job each; functions that do one thing; no import-time side effects; `if __name__ == "__main__":` guards scripts.
- Avoid global mutable state; pass dependencies in. Prefer composition to inheritance.
- `pytest` (or `unittest` if the project does): synthetic fixtures, `tmp_path` for files, `monkeypatch`/`mock.patch.dict` for env, every resource cleaned up. Parametrize instead of copy-pasting cases. Coverage floor from the project config.

## Tooling (must be clean)
`ruff check`, `ruff format --check`, `mypy`, `pytest`, plus `bandit`, `vulture` and `pip-audit` where the project uses them.

## Check in review
Mutable default, broad `except`, shell string, unvalidated input, resource without `with`, naive datetime, float money, print/debug left in, missing type on a public function, a test that cannot fail.
