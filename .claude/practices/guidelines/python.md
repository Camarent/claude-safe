# Python guidelines

Digest of PEP 8, PEP 20, PEP 484/526 typing, and the ruff/mypy rule sets. Gates and tools are in `practices/python.md`. The project's own CLAUDE.md and pyproject win. Language-neutral rules (TDD cycle, DRY, SOLID limits, errors, logs, state) are in `base.md`; this file adds the Python forms of them and Python-only rules.

## Style
- PEP 8 names: `snake_case` functions and variables, `CapWords` classes, `UPPER_CASE` constants. Let `ruff format` own layout; do not hand-format.
- Explicit is better than implicit; flat is better than nested; errors never pass silently (PEP 20).
- f-strings for formatting; `pathlib.Path` for paths; `with` for every resource; comprehensions only when they stay readable on one line or two.
- Do not use mutable default arguments (`def f(x=[])`); use `None` and create inside. Do not shadow builtins (`id`, `type`, `list`).
- Stdlib first: `dataclasses`, `enum`, `itertools`, `functools`, `collections`, `argparse`, `subprocess.run([...], check=True)`. No new dependency for a few lines.
- Imports: absolute, grouped stdlib / third party / local (`ruff` sorts them). No `import *`. No import cycles: move the shared piece to a third module.
- Docstrings on public API only: the contract (inputs, units, raises, side effects), not the story of how it was written.

## Types
- Annotate public functions and anything crossing a module boundary; keep annotations accurate (`list[str]`, `X | None`, `Sequence`, `Mapping` for inputs; concrete types for returns). `mypy` clean; no blanket `# type: ignore` (give the code and a reason).
- `@dataclass(frozen=True, slots=True)` (3.10+) for plain data; `TypedDict` or a model for parsed JSON; `Protocol` for duck-typed seams instead of inheritance. `Any` only at a real dynamic edge.
- Parse and validate external input once at the boundary (pydantic or explicit checks), then pass typed objects.

## Errors and safety
- Catch the narrowest exception; no bare `except:` and no `except Exception: pass`. Re-raise with `raise ... from err`. Custom exceptions subclass a project base. Use exceptions, not return codes.
- Never build shell or SQL from strings: `subprocess` with an argument list (no `shell=True`), parameterised queries. `yaml.safe_load`, never `pickle.loads` on untrusted data. Use `secrets` for tokens, not `random`.
- No `assert` for runtime validation (stripped with `-O`). Never `print` in library code.
- Logging: `logger = logging.getLogger(__name__)` per module; lazy `%s` arguments; no `basicConfig` in library code. Use `logger.exception` inside the handler that swallows an error.
- Time: timezone-aware `datetime` in UTC; money with `Decimal` or integers.

## Structure
- Small modules, one job each; functions that do one thing; no import-time side effects; `if __name__ == "__main__":` guards scripts.

## DRY and SOLID in Python
- Seam = `Protocol` or a plain callable argument (`clock: Callable[[], datetime]`), not an abstract base class with one subclass.
- Module-level function over a class with one method and no state. `@dataclass` for data; add behaviour as functions or small methods.
- Open/closed: a dict or table of `{key: handler}` (or `functools.singledispatch`) instead of an `if/elif` chain on type or name. Adding a case is one entry.
- No god module and no `utils.py` or `helpers.py` dump: name the module for its job (`pricing.py`, `retry.py`).
- Reach for `functools` (`partial`, `cache`, `reduce`), `itertools` (`chain`, `groupby`, `batched` (3.12+)), `any`/`all`, `sum` before a hand-written accumulator loop.

## Async and concurrency
- Never call blocking code in a coroutine (`time.sleep`, `requests`, file or DB drivers without async support): use `asyncio.to_thread` or an async library.
- Structured concurrency: `asyncio.TaskGroup` (3.11+) over loose `create_task`; keep a reference to every task you create; bound waits with `asyncio.timeout`.
- Cancellation: let `asyncio.CancelledError` propagate (re-raise after cleanup); release resources in `finally` or `async with`.
- Shared state across threads needs a `threading.Lock` or a `queue.Queue`; across processes pass messages, not objects. Prefer immutable data.

## Tests (TDD)
- Red-green-refactor with `pytest`. Run one test first: `pytest path::test_name -x` (stop at first failure; `-k expr` selects by name, `--lf` reruns last failures). Run the full suite before you finish.
- Red must fail on the assertion, not on `ImportError` or `NameError`. Add the stub, then see the real failure.
- Fixture for shared setup and teardown (`yield` then clean up); plain helper or factory function for building data with overrides. Keep fixtures function-scoped and not mutated across tests.
- `@pytest.mark.parametrize` with `ids=` (or `pytest.param(..., id="...")`) instead of copy-paste or a loop inside one test.
- `tmp_path` for files; `monkeypatch.setenv` / `setattr` / `chdir` for env and attributes (undone for you); in `unittest`, `mock.patch.dict(os.environ, ...)` for env; `caplog` to assert log records; `pytest.raises(Err, match="...")` to pin the message.
- Time: inject a clock (callable or `Protocol`) and pass a fixed value. Patch the clock only for code you cannot change.
- Mock only at the boundary (network, clock, process, third-party). Use `unittest.mock.patch` where the name is looked up, with `autospec=True` so a wrong signature fails. Never mock your own code; use a small fake.
- No `time.sleep`, no network, no real home directory. Wait on an event or poll with a deadline. Mark slow tests `@pytest.mark.slow` (register the marker) so `-m "not slow"` gives a fast loop.
- Database: scratch database per test (in-memory SQLite or a temp schema), created in a fixture and dropped at teardown. Roll back or drop even on failure.
- Async tests: use the project's plugin (`pytest-asyncio` or `anyio`); do not call `asyncio.run` inside a test body under a running loop.
- Property tests with `hypothesis` (`@given`) where a parser, serializer or invariant exists (round trip, idempotence, ordering). Pin a found failure as a plain example test.
- Mutation check by hand: flip a condition or delete a line, run the test, see it go red, revert.
- `unittest` projects: `addCleanup` for every resource, `subTest` for cases. Coverage floor from the project config.

## Tooling (must be clean)
`ruff check`, `ruff format --check`, `mypy`, `pytest`, plus `bandit`, `vulture` and `pip-audit` where the project uses them.

## Check in review
Mutable default, broad `except`, shell string, unvalidated input, resource without `with`, naive datetime, float money, print/debug left in, missing type on a public function, a long `if/elif` chain on type or name, a one-method class with no state, `utils.py` growth, a shared mutable fixture, a blocking call in a coroutine, an orphan `create_task`, `import *` or an import cycle, `sleep` in a test.
