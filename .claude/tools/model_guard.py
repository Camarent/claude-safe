#!/usr/bin/env python3
"""model_guard: PreToolUse hook for Agent. Enforces the ORCHESTRATOR "Haiku first" rule.

Denies an Agent call when
1. `model` is missing (the agent would inherit the lead's model), fork included, or
2. `model` is not haiku and the prompt has no line `MODEL-REASON: <logic written or changed, and what>`, or
3. the agent type is ops or scout (mechanical by definition) and the model is not haiku.

Haiku = `haiku` or a `claude-haiku-*` id, after strip and lowercase. Fork is not exempt.
A malformed hook input (non-object tool_input, non-string model, subagent_type or prompt) exits 2 with the reason on stderr.

Setup: settings.json runs `python3 -I /root/.claude/tools/model_guard.py`; if that path is missing python exits 2 and blocks every Agent
call, so restore the file (or fix the hook path) rather than the guard.

Reads the hook JSON on stdin; prints a deny decision, or nothing to allow. Exit 0, except 2 for malformed input.
"""

import json
import re
import sys

REASON_LINE = re.compile(r"^\s*MODEL-REASON:\s*\S.{11,}", re.M)
HAIKU_ID = re.compile(r"claude-haiku-[\w.-]+")
MECHANICAL = {"ops", "scout"}


def is_haiku(model):
    m = model.strip().lower()
    return m == "haiku" or HAIKU_ID.fullmatch(m) is not None


def malformed(tool_input):
    """-> reason when the hook input has the wrong shape, else ''."""
    if not isinstance(tool_input, dict):
        return "model_guard: tool_input must be a JSON object; the Agent call cannot be checked."
    for key in ("model", "subagent_type", "prompt"):
        if tool_input.get(key) is not None and not isinstance(tool_input.get(key), str):
            return f"model_guard: `{key}` must be a string; the Agent call cannot be checked."
    return ""


def verdict(tool_input):
    """-> deny reason or ''."""
    bad = malformed(tool_input)
    if bad:
        return bad
    kind = tool_input.get("subagent_type") or ""
    model = (tool_input.get("model") or "").strip()
    if not model:
        return "Set `model` explicitly on every Agent call (Haiku first, see ORCHESTRATOR.md 'Models'). Omitting it inherits the lead's model."
    if is_haiku(model):
        return ""
    if kind in MECHANICAL:
        return f"`{kind}` is a mechanical agent: use model haiku. Escalation goes to dev/general-purpose with a reason."
    if not REASON_LINE.search(tool_input.get("prompt") or ""):
        return (
            f"model {model} needs a line `MODEL-REASON: <what new logic is written or changed>` in the prompt. "
            "Transcription, extraction, counting, running tools and diffing are mechanical: use haiku (ops for bulk)."
        )
    return ""


def main():
    try:
        tool_input = json.load(sys.stdin).get("tool_input", {})
    except (ValueError, AttributeError):
        return 0
    if tool_input is None:
        tool_input = {}
    bad = malformed(tool_input)
    if bad:
        print(bad, file=sys.stderr)
        return 2
    why = verdict(tool_input)
    if why:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
