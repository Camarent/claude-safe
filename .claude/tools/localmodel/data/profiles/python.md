Python principles:
- Standard library first; no new dependency. Follow the module's existing style and naming.
- Function over 50 lines or more than 4 parameters: split. Early returns over deep nesting (3 at most).
- Tests use `unittest`; run the single named test file first, the whole suite once at the end.
- Do not catch broad exceptions to make a test pass; validate inputs at the boundary and raise a specific error.
- Round and format money, dates and percentages with explicit rules named in the brief (half-up vs half-even matters).
