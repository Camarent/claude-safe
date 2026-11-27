# Error handling: kept swallow sites

An `except` that swallows an error needs one of three things:

- Re-raise when the caller must see the failure.
- Return an error value or status that the caller reports (for example status `crash`, exit code 3).
- Call `swallowed(where, exc)` (`.claude/tools/pipeline/util.py:163`) when the failure is best-effort and the code goes on. It leaves one `swallowed in <where>` line on stderr.

A silent `except` with no reason is a bug. When a silent swallow stays on purpose, put `# KEEP: <reason>` on the `except` line and list the site below. `test_error_handling_doc.py` checks that every row points at a real `except` line and that every `# KEEP:` in the code has a row.

The script-step timeout (`script_timeout()` in `.claude/tools/pipeline/util.py`) is the `script_timeout_s` tool setting (`tool_settings` in pipeline.json), and the env `ORCH_SCRIPT_TIMEOUT` overrides it. A timed-out acceptance step is BLOCKED `timed out, not verified`, never FAIL; any other timed-out driver step is FAIL.

## search

- `.claude/tools/search/lsp.py:144` - rg prints one JSON object per line; a non-JSON line is skipped, not an error
- `.claude/tools/search/lsp.py:195` - checker missing or hung; the NOT CHECKED line below reports it
- `.claude/tools/search/lsp.py:311` - pull diagnostics is optional; the push settle below covers servers without it
- `.claude/tools/search/lsp_client.py:99` - not a swallow; a refused or silent initialize must not leave the server running, then re-raise
- `.claude/tools/search/codesrv.py:142` - logs the traceback to stderr and reports exit 3 to the caller
- `.claude/tools/search/codesrv.py:146` - last resort for one connection; logs the traceback so the server keeps serving
- `.claude/tools/search/codesrv.py:168` - connect fails when no server listens on the socket; then this one starts

## localmodel

- `.claude/tools/localmodel/plan_context.py:99` - no usable json is the documented None answer
- `.claude/tools/localmodel/plan_context.py:107` - git cannot run is the documented None answer
- `.claude/tools/localmodel/plan_context.py:123` - no usable json is the documented None answer

## audit

- `.claude/tools/audit/audit_parsers.py:27` - not a plain JSON document (log noise first); scan_json finds the document
- `.claude/tools/audit/audit_run.py:123` - returned as status "crash" with the text; the caller reports it
- `.claude/tools/audit/audit_run.py:141` - no git means no short sha in the stamp; the date stays
- `.claude/tools/audit/gatewatch.py:25` - the file was removed between the walk and this stat, a normal race

## top-level

No kept silent swallow in the top-level tools. Their best-effort paths call `swallowed()` and log one stderr line.
