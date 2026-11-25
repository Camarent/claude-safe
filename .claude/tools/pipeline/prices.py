"""Price table: USD per million tokens by model family. ledger.py and agent_cost.py use it.

The rows are the tool setting PRICES_TABLE (configuration/literals.py: the default rows, and pipeline.json `tool_settings.prices` overrides them).
PRICES_DATE says when the default rows were set; PRICES MUST BE RE-CHECKED against the published list before any number here is used for a decision.
"""

from .configuration.literals import tool_setting

PRICES_DATE = "2026-10-30"

# (substring of the model id, input, output, cache_read, cache_write); first match wins, so specific rows come first.
PRICES = tool_setting("PRICES_TABLE")

# Fallback for ids not in the table: USD per ledger weighted unit (1000 weighted tokens), derived from ONE Opus run
# ($4.4/M input at weight 5, so 0.88 per weight point per M); a rough estimate, not a price (backlog 37).
DERIVED_USD_PER_UNIT = 0.88 / 1000.0


def lookup(model):
    """-> {input, output, cache_read, cache_write} USD per million tokens, or None for an unknown model."""
    m = (model or "").lower()
    for key, i, o, r, w in PRICES:
        if key in m:
            return {"input": i, "output": o, "cache_read": r, "cache_write": w}
    return None


def cost(model, input=0, output=0, cache_read=0, cache_write=0):
    """-> USD from the price table, or None when the model has no row (the caller falls back and must say so)."""
    p = lookup(model)
    if p is None:
        return None
    return (input * p["input"] + output * p["output"] + cache_read * p["cache_read"] + cache_write * p["cache_write"]) / 1e6
