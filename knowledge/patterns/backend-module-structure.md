---
id: backend-module-structure
kind: pattern
applies_to:
  - codegen_backend
  - codegen_review
summary: >
  Use when adding or reviewing Python in packages/, api/, or worker/. Covers the layering
  rule, where I/O is allowed, error handling, and the two conventions enforced by CI.
triggers:
  always: true
version: 1
status: active
priority: 20
evidence: scripts/check_conventions.py
review_by: 2027-02-01
owner: backend
---

# Backend module structure

## Layering

```
domain/        pure logic. No database, no network, no clock.
services/      orchestration. I/O lives here.
repositories/  the only place SQL is written.
routers/       HTTP shape only — parse, delegate, serialize.
```

The parts most likely to be subtly wrong — grid invariants, progress merge, streak
arithmetic — live in `domain/` precisely so they can be tested with a plain function
call. If a `domain/` module needs the current time or a database row, that is the signal
its caller should be passing the value in.

## The two rules CI enforces

**No prompt text in application code.** Any string literal over 400 characters fails
`scripts/check_conventions.py`. Prompt content is a versioned document under
`knowledge/`; application code assembles documents, it never contains them. The check
folds `"..." * 40` and `"a" + "b"`, so concatenation is not a way around it.

**No direct Anthropic SDK imports.** Everything goes through
`braingames_core.llm.LLMClient`, which requires a stage label and a schema. `llm.py` is
the sole allowlisted file.

Both rules have a purpose beyond tidiness: they are what stop the knowledge base from
being quietly bypassed one convenient exception at a time.

## Configuration

Read config through `braingames_core.config.settings()`. No `os.environ` anywhere else —
a missing required setting must fail at startup with a readable message, not at 3am on
first use inside a nightly run.

## Errors

Raise a named exception with a message that says what to do next, not just what went
wrong:

```python
raise RequiredDocsOverflowError(
    f"Required documents for {stage.value} need {required_cost} tokens but the "
    f"budget is {limit}.\n  {listing}\n"
    f"Raise STAGE_BUDGETS[{stage.value}] or shorten these documents."
)
```

The reader of that message is someone debugging at speed with no context. Name the
values, name the fix.

Do not catch broadly to keep a pipeline running. Every stage of the generation DAG
persists its output and can resume; a stage that swallows an error to avoid failing
produces a bad puzzle instead of a missing one, and a bad puzzle is worse — the evergreen
fallback handles missing.

## Logging and tracing

`braingames_core.obs`. Bind a correlation id at the top of any unit of work, use
`obs.span()` per pipeline stage, and log structured events rather than formatted prose:

```python
with obs.correlation(run=run_id, cohort_id=cohort.id):
    with obs.span("generate.theme_ideation", stage="theme_ideation", tier=tier):
        log.info("stage_started", extra={"template_id": template.id})
```

The generation DAG as one trace with a span per stage is the single most useful artifact
when a cohort's puzzles come out badly. It only exists if spans are added as stages are
written.

## Typing

`mypy --strict` passes. When a third-party signature is stricter than our public API — as
with the Anthropic SDK's TypedDicts — cast at exactly one boundary and comment why, rather
than loosening our own types outward.

## Tests

- `domain/` gets property tests. Arbitrary action sequences must never reach an invalid
  state.
- Anything with a silent failure mode gets a test that proves the failure is loud:
  volatile prompt content, budget overruns, unmeasured token counts.
- A test that asserts current behaviour is not the same as a test that asserts required
  behaviour. Say which one you are writing in the docstring.
