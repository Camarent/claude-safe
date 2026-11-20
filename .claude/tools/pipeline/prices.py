"""Price table: USD per million tokens by model family. The only place prices live (ledger.py and agent_cost.py use it).

SET 2026-10-30 from the claude-api skill's cached model table (cached 2026-10-06). PRICES MUST BE RE-CHECKED against the
published list before any number here is used for a decision: list prices change with each model release.

Sure: input and output for every row, cache read for the two 5.5 rows that list it ($0.20).
Assumed (not listed in the source): cache write = 1.25 x input (5-minute TTL), cache read = 0.1 x input where not listed.
Haiku 5.5 input/output are for prompts up to 100K tokens ($0.50 / $2.50 beyond; not modelled).
"""

PRICES_DATE = "2026-10-30"

# (substring of the model id, input, output, cache_read, cache_write); first match wins, so specific rows come first.
PRICES = (
    ("opus-5-5", 4.00, 20.00, 0.20, 5.00),
    ("opus", 5.00, 25.00, 0.50, 6.25),  # opus 5 / 4.8 / 4.7 / 4.6
    ("sonnet-5", 2.00, 10.00, 0.20, 2.50),  # sonnet 5.5 and 5
    ("sonnet", 3.00, 15.00, 0.30, 3.75),  # sonnet 4.6 and older
    ("haiku-5-5", 0.10, 0.50, 0.01, 0.125),
    ("haiku", 1.00, 5.00, 0.10, 1.25),  # haiku 4.5
)

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
