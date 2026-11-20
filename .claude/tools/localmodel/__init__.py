"""Local-model (Strata) tools: plan_context, context_eval, find_eval, prereview, localdev, plus eval/ and data/ (localdev policy and profiles).

The core pipeline does not depend on them. plan_run imports plan_context lazily inside a try block and skips the step when it fails or the model is
down; prereview is opt-in through pipeline.json. Run an entry script as `python3 -I tools/localmodel/<name>.py`.
"""
