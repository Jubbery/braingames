"""The single metered entry point to the Anthropic API.

Convention #2 from docs/12-development-plan.md §12.2: *every* model call is
schema-constrained and metered. Nothing else in this codebase imports the
Anthropic SDK directly — a lint rule enforces it.

Two things this wrapper makes non-optional:

1. A ``stage`` label, so spend is attributable.
2. A structured output schema. There is no unstructured generation method,
   so nothing downstream can end up parsing free-form prose.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, TypeVar, cast

import anthropic
from pydantic import BaseModel

from . import obs
from .config import Settings, settings
from .costs import CostSink, LLMCall, Usage, compute_cost, make_sink

T = TypeVar("T", bound=BaseModel)

log = obs.get_logger(__name__)

SystemBlock = dict[str, Any]


class LLMClient:
    """Metered, schema-enforcing Anthropic client."""

    def __init__(
        self,
        *,
        config: Settings | None = None,
        sink: CostSink | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.config = config or settings()
        self.sink = sink or make_sink(self.config.cost_sink, self.config.cost_log_path)
        # A bare constructor is correct: the SDK resolves credentials from
        # ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login`
        # profile. Passing api_key=None explicitly would break the profile path.
        if client is not None:
            self._client = client
        elif self.config.anthropic_api_key:
            self._client = anthropic.Anthropic(api_key=self.config.anthropic_api_key)
        else:
            self._client = anthropic.Anthropic()

    # ------------------------------------------------------------------
    # Token counting
    # ------------------------------------------------------------------

    def count_tokens(
        self,
        text: str,
        *,
        model: str | None = None,
        stage: str = "token_count",
    ) -> int:
        """Measure tokens for a block of text.

        Used to populate the ``tokens`` field on knowledge documents. Measured,
        never estimated — a budget built on a guess is not a budget. Note that
        ``tiktoken`` and friends are OpenAI tokenizers and are wrong for Claude
        by 15-20% on prose and much more on code.
        """
        model = model or self.config.model_token_reference
        started = time.monotonic()
        with obs.span("llm.count_tokens", model=model, stage=stage):
            response = self._client.messages.count_tokens(
                model=model,
                messages=[{"role": "user", "content": text}],
            )
        self._record(
            stage=stage,
            model=model,
            usage=Usage(input_tokens=response.input_tokens),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return int(response.input_tokens)

    # ------------------------------------------------------------------
    # Structured generation
    # ------------------------------------------------------------------

    def structured(
        self,
        *,
        stage: str,
        model: str,
        schema: type[T],
        system: Sequence[SystemBlock] | str,
        messages: list[dict[str, Any]],
        max_tokens: int = 16000,
        effort: str = "high",
        thinking: dict[str, Any] | None = None,
        run_id: str | None = None,
        cohort_id: str | None = None,
        kb_version: str | None = None,
        cache_ttl: str = "5m",
    ) -> T:
        """Make a schema-constrained call and return a validated model.

        Notes on the request shape, since these are the details that go stale:

        * ``thinking`` defaults to adaptive. Do not pass ``budget_tokens`` —
          it is removed on Opus 5 / Sonnet 5 and returns a 400.
        * ``effort`` lives inside ``output_config``, never at the top level.
        * No ``temperature`` / ``top_p`` / ``top_k``. Sampling parameters are
          rejected on current models; steer with prompting instead.
        * ``max_tokens`` caps thinking *plus* response text. Thinking is on by
          default on Opus 5, so size it for both.
        """
        started = time.monotonic()
        error: str | None = None
        usage = Usage()

        # The SDK's TypedDict params are stricter than the plain dicts this
        # wrapper accepts, and deliberately so: callers should not have to
        # import Anthropic types to use it. The cast is the one place that
        # boundary is crossed, so it stays narrow and visible.
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "thinking": thinking or {"type": "adaptive"},
            "output_config": {
                "effort": effort,
                "format": {
                    "type": "json_schema",
                    "schema": schema.model_json_schema(),
                },
            },
            "system": system,
            "messages": messages,
        }

        try:
            with obs.span(
                f"llm.{stage}",
                model=model,
                effort=effort,
                kb_version=kb_version,
                cohort_id=cohort_id,
            ):
                response = self._client.messages.create(**cast(Any, request))
                usage = _usage_from(response)
                obs.set_span_attributes(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_input_tokens,
                    cache_hit_ratio=round(usage.cache_hit_ratio, 3),
                )

                if response.stop_reason == "refusal":
                    detail = getattr(response, "stop_details", None)
                    category = getattr(detail, "category", None)
                    raise RuntimeError(
                        f"Model declined the request (category={category!r}) at stage {stage!r}."
                    )

                text = next(
                    (b.text for b in response.content if getattr(b, "type", None) == "text"),
                    None,
                )
                if text is None:
                    raise RuntimeError(
                        f"No text block in response at stage {stage!r} "
                        f"(stop_reason={response.stop_reason!r})."
                    )
                return schema.model_validate_json(text)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._record(
                stage=stage,
                model=model,
                usage=usage,
                duration_ms=duration_ms,
                run_id=run_id,
                cohort_id=cohort_id,
                kb_version=kb_version,
                cache_ttl=cache_ttl,
                error=error,
            )
            # One line per call, correlated. This is what makes a bad nightly
            # run reconstructable from logs alone.
            log.info(
                "llm_call",
                extra={
                    "stage": stage,
                    "model": model,
                    "duration_ms": duration_ms,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_input_tokens,
                    "cache_hit_ratio": round(usage.cache_hit_ratio, 3),
                    "error": error,
                },
            )

    # ------------------------------------------------------------------

    def _record(
        self,
        *,
        stage: str,
        model: str,
        usage: Usage,
        duration_ms: int,
        batch: bool = False,
        cache_ttl: str = "5m",
        run_id: str | None = None,
        cohort_id: str | None = None,
        kb_version: str | None = None,
        error: str | None = None,
    ) -> None:
        self.sink.record(
            LLMCall(
                stage=stage,
                model=model,
                usage=usage,
                batch=batch,
                cache_ttl=cache_ttl,
                cost_usd=compute_cost(model, usage, batch=batch, cache_ttl=cache_ttl),
                duration_ms=duration_ms,
                run_id=run_id or obs.run_id(),
                correlation_id=obs.correlation_id(),
                cohort_id=cohort_id,
                kb_version=kb_version,
                error=error,
            )
        )


def _usage_from(response: Any) -> Usage:
    u = getattr(response, "usage", None)
    if u is None:
        return Usage()
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
    )


__all__ = ["LLMClient", "SystemBlock"]
