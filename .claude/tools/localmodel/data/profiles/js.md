JavaScript principles (vanilla ES modules, Preact-style UI, no TypeScript):
- File 300 lines at most (split above 500); function 50 lines (split above 100); cyclomatic complexity 10; nesting 3; parameters 3 (use an options object above 4).
- Preact component under 100 lines with at most 5 props; no import cycles; no new dependency.
- Tests use `node --test`. A test that depends on a missing file must skip dynamically, never with a hard-coded skip.
- Text shown to users comes from the existing label/format helpers, not inline strings; do not change shown wording unless the brief says so.
- Keep exported names stable; if you move code, re-export from the old module.
