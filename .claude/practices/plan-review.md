# Plan review: second-model check with one fix round

Read when changing plan review, its settings, or the plan-reviewer agent. Decision record for stage pipeline-efficiency/phases/07-plan-review.

## What is reviewed

Four review points. Each runs once per plan, after the plan check passes.

| Point | Kind | Model setting | Notes |
|---|---|---|---|
| Phase plan (big feature, `Plan.md` exists) | `phase` | `PLAN_REVIEW_MODEL_LIGHT` (Haiku) | Checks the phase plan against the request. |
| Task plan (each normal phase) | `task` | `PLAN_REVIEW_MODEL_LIGHT` (Haiku) | Checks each goal item has a task, each task has a `Test:` line, and `Depends:` is present. |
| Last phase cover (open phase before the finish phase) | `cover` | `PLAN_REVIEW_MODEL_HEAVY` (Sonnet) | Checks coverage and the task count limit (see below). |
| One-phase feature (one stage, no `Plan.md`) | `single` | `PLAN_REVIEW_MODEL_LIGHT` (Haiku) when task count is at most `PLAN_REVIEW_SMALL_MAX_TASKS`, else `PLAN_REVIEW_MODEL_HEAVY` (Sonnet) | Small features stay cheap. |

## Why a second model

The architect misses items from the prompt. Evidence: lessons recorded in the kata pipeline notes (`pipeline-efficiency-lessons.md`). A second reviewer model reads the request and the plan with fresh context and lists each gap.

## Why one fix round and no re-review

- One fix round bounds cost: at most one reviewer call and one architect fix call per review point.
- A fix round is not re-reviewed. Gaps that remain after the fix are printed and logged (`plan-review-gaps.md` in the phase folder for task-plan review). The run continues; the lead sees the gaps.
- The plan check still runs after the fix, so structural errors are still caught.

## Blocking rule (last-phase cover only)

The cover review returns `BLOCKED` when:

    current_tasks + gap_tasks > max_tasks + PLAN_EXTRA_TASKS_ALLOWED

- `current_tasks` is the task count of the last phase.
- `gap_tasks` comes from the report line `GAP-TASKS: <n>`. If that line is missing, the count of `GAP:` lines is used.
- `max_tasks` is the project limit (key `max_tasks` in `defaults.py`). It has no new setting.
- Equal to the limit plus extra is not blocked. One more task is blocked.

A `BLOCKED` result stops the feature with the message `plan-review BLOCKED: another phase needed`. The lead decides.

## Verdict format

The reviewer writes its report to the report path named in the prompt. A `VERDICT:` line ends the report (the parser takes the last one). It is one of:

    VERDICT: DONE
    VERDICT: FAIL
    VERDICT: BLOCKED

Gap lines use `GAP: <text>`. The cover review also writes `GAP-TASKS: <n>`.

The parser takes the last `VERDICT:` line. A missing or unknown verdict gives an empty verdict: no fix call, no exception, the run continues. A dispatch exception gives verdict `ERROR`, is logged, and the run continues.

## Settings

All limits are named settings in `.claude/tools/pipeline/configuration/limits.py`, exported from `configuration/__init__.py` next to `REVIEW_EXTRA_ROUNDS`.

- `PLAN_REVIEW_ROUNDS`: fix rounds per review point. Default 1.
- `PLAN_REVIEW_SMALL_MAX_TASKS`: largest task count for a one-phase feature that uses the light model. Default 7.
- `PLAN_EXTRA_TASKS_ALLOWED`: extra tasks allowed above `max_tasks` before cover review blocks. Default 10.
- `PLAN_REVIEW_MODEL_LIGHT`: model for phase, task, and small single review. Default Haiku.
- `PLAN_REVIEW_MODEL_HEAVY`: model for cover and big single review. Default Sonnet.

No literal model name or limit number lives in the review code. Change a setting in `limits.py` or through the pipeline configuration override.

## Agent

`.claude/agents/plan-reviewer.md` holds the reviewer prompt and the report format. It writes only the report file.
