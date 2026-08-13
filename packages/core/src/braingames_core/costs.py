"""Cost accounting for every model call.

Built in week one on purpose. Generation spend is the main variable cost of the
business, and ``cache_read_input_tokens`` is the canary for the prompt-assembly
discipline in docs/11-agent-knowledge-base.md §11.5 — if it trends toward zero,
something above a cache breakpoint became volatile.

See docs/12-development-plan.md P0.4.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

# Per million tokens, Anthropic first-party list prices.
# Partner platforms (Bedrock, Vertex) price separately — see docs/live pricing.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-fable-5": (Decimal("10.00"), Decimal("50.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-8": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": (Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
}

# Claude Sonnet 5 introductory pricing, in effect through this date inclusive.
_SONNET_5_INTRO = (Decimal("2.00"), Decimal("10.00"))
_SONNET_5_INTRO_UNTIL = date(2026, 8, 31)

# Cache reads bill at ~0.1x input; writes at 1.25x (5m TTL) or 2x (1h TTL).
CACHE_READ_MULTIPLIER = Decimal("0.10")
CACHE_WRITE_MULTIPLIER_5M = Decimal("1.25")
CACHE_WRITE_MULTIPLIER_1H = Decimal("2.00")


def prices_for(model: str, on: date | None = None) -> tuple[Decimal, Decimal]:
    """Input/output price per million tokens, honouring dated promotions."""
    on = on or datetime.now(UTC).date()
    if model == "claude-sonnet-5" and on <= _SONNET_5_INTRO_UNTIL:
        return _SONNET_5_INTRO
    try:
        return PRICES[model]
    except KeyError as exc:
        raise KeyError(
            f"No price on record for model {model!r}. Add it to PRICES rather than "
            f"letting spend go unmeasured."
        ) from exc


class Usage(BaseModel):
    """The subset of an Anthropic ``usage`` object we account for."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_prompt_tokens(self) -> int:
        """Full prompt size.

        ``input_tokens`` is only the *uncached remainder*. Reading it alone
        under-reports a well-cached call by an order of magnitude.
        """
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def cache_hit_ratio(self) -> float:
        total = self.total_prompt_tokens
        return self.cache_read_input_tokens / total if total else 0.0


class LLMCall(BaseModel):
    """One metered model call."""

    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stage: str
    model: str
    usage: Usage
    batch: bool = False
    cache_ttl: str = "5m"
    cost_usd: Decimal = Decimal("0")
    duration_ms: int = 0
    run_id: str | None = None
    correlation_id: str | None = None
    cohort_id: str | None = None
    kb_version: str | None = None
    error: str | None = None


def compute_cost(
    model: str,
    usage: Usage,
    *,
    batch: bool = False,
    cache_ttl: str = "5m",
    on: date | None = None,
) -> Decimal:
    """List-price cost of a single call, in USD."""
    in_price, out_price = prices_for(model, on)
    million = Decimal(1_000_000)
    write_mult = CACHE_WRITE_MULTIPLIER_1H if cache_ttl == "1h" else CACHE_WRITE_MULTIPLIER_5M

    cost = (
        Decimal(usage.input_tokens) * in_price
        + Decimal(usage.cache_read_input_tokens) * in_price * CACHE_READ_MULTIPLIER
        + Decimal(usage.cache_creation_input_tokens) * in_price * write_mult
        + Decimal(usage.output_tokens) * out_price
    ) / million

    if batch:
        cost *= Decimal("0.5")
    return cost.quantize(Decimal("0.000001"))


class CostSink(Protocol):
    def record(self, call: LLMCall) -> None: ...
    def read_all(self) -> Iterator[LLMCall]: ...


class NullSink:
    """Discards records. For unit tests that don't assert on accounting."""

    def record(self, call: LLMCall) -> None:
        return None

    def read_all(self) -> Iterator[LLMCall]:
        return iter(())


class JsonlSink:
    """Append-only local log.

    Deliberately not a database. The Postgres sink lands in P4.1; until then
    this keeps the meter working from day one rather than waiting on schema.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, call: LLMCall) -> None:
        line = call.model_dump_json() + "\n"
        # Append is atomic for small writes on POSIX with O_APPEND, which is
        # what we need for concurrent workers.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def read_all(self) -> Iterator[LLMCall]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if raw:
                    yield LLMCall.model_validate_json(raw)


class MemorySink:
    """In-process sink for tests."""

    def __init__(self) -> None:
        self.calls: list[LLMCall] = []

    def record(self, call: LLMCall) -> None:
        self.calls.append(call)

    def read_all(self) -> Iterator[LLMCall]:
        return iter(self.calls)


def make_sink(kind: str, path: Path) -> CostSink:
    match kind:
        case "jsonl":
            return JsonlSink(path)
        case "null":
            return NullSink()
        case "memory":
            return MemorySink()
        case _:
            raise ValueError(f"Unknown cost sink {kind!r} (expected jsonl | null | memory)")


class StageRollup(BaseModel):
    stage: str
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: Decimal = Decimal("0")

    @property
    def cache_hit_ratio(self) -> float:
        total = self.input_tokens + self.cached_tokens
        return self.cached_tokens / total if total else 0.0


def rollup(
    calls: Iterator[LLMCall],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[StageRollup]:
    """Aggregate calls by (stage, model)."""
    buckets: dict[tuple[str, str], StageRollup] = {}
    for call in calls:
        if since and call.ts < since:
            continue
        if until and call.ts > until:
            continue
        key = (call.stage, call.model)
        b = buckets.setdefault(key, StageRollup(stage=call.stage, model=call.model))
        b.calls += 1
        b.input_tokens += call.usage.input_tokens + call.usage.cache_creation_input_tokens
        b.output_tokens += call.usage.output_tokens
        b.cached_tokens += call.usage.cache_read_input_tokens
        b.cost_usd += call.cost_usd
    return sorted(buckets.values(), key=lambda r: r.cost_usd, reverse=True)


def atomic_write(path: Path, content: str) -> None:
    """Write a file atomically. Used when rewriting knowledge documents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


__all__ = [
    "CostSink",
    "JsonlSink",
    "LLMCall",
    "MemorySink",
    "NullSink",
    "StageRollup",
    "Usage",
    "atomic_write",
    "compute_cost",
    "make_sink",
    "prices_for",
    "rollup",
]
