"""Cost accounting.

The numbers here decide whether the business works, so the arithmetic is
tested rather than trusted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from braingames_core.costs import (
    JsonlSink,
    LLMCall,
    MemorySink,
    Usage,
    compute_cost,
    make_sink,
    prices_for,
    rollup,
)


def test_input_tokens_is_only_the_uncached_remainder() -> None:
    """Reading input_tokens alone under-reports a cached call by an order of magnitude."""
    usage = Usage(
        input_tokens=4_000,
        cache_read_input_tokens=33_800,
        cache_creation_input_tokens=2_200,
        output_tokens=1_000,
    )
    assert usage.total_prompt_tokens == 40_000
    assert usage.cache_hit_ratio == pytest.approx(0.845)


def test_price_lookup() -> None:
    assert prices_for("claude-opus-5") == (Decimal("5.00"), Decimal("25.00"))
    assert prices_for("claude-haiku-4-5") == (Decimal("1.00"), Decimal("5.00"))


def test_sonnet_5_intro_pricing_is_dated() -> None:
    assert prices_for("claude-sonnet-5", date(2026, 8, 1)) == (Decimal("2.00"), Decimal("10.00"))
    assert prices_for("claude-sonnet-5", date(2026, 9, 1)) == (Decimal("3.00"), Decimal("15.00"))


def test_unknown_model_refuses_rather_than_guessing() -> None:
    with pytest.raises(KeyError, match="unmeasured"):
        prices_for("claude-imaginary-9")


def test_simple_cost() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert compute_cost("claude-opus-5", usage) == Decimal("30.000000")


def test_cache_reads_are_cheap_and_writes_are_dear() -> None:
    read = compute_cost("claude-opus-5", Usage(cache_read_input_tokens=1_000_000))
    write = compute_cost("claude-opus-5", Usage(cache_creation_input_tokens=1_000_000))
    full = compute_cost("claude-opus-5", Usage(input_tokens=1_000_000))

    assert read == Decimal("0.500000")  # 0.1x
    assert write == Decimal("6.250000")  # 1.25x at 5m TTL
    assert full == Decimal("5.000000")
    assert read < full < write


def test_one_hour_ttl_costs_more_to_write() -> None:
    usage = Usage(cache_creation_input_tokens=1_000_000)
    assert compute_cost("claude-opus-5", usage, cache_ttl="1h") == Decimal("10.000000")


def test_batch_halves_the_bill() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000)
    standard = compute_cost("claude-sonnet-5", usage, on=date(2026, 9, 1))
    batched = compute_cost("claude-sonnet-5", usage, batch=True, on=date(2026, 9, 1))
    assert batched == standard / 2


def test_realistic_puzzle_cost_matches_the_design_estimate() -> None:
    """Sanity-check docs/04 §4.8: a 15x15 puzzle should land near $0.18."""
    ideation = (
        compute_cost(
            "claude-opus-5",
            Usage(input_tokens=2_000, cache_read_input_tokens=7_000, output_tokens=4_000),
        )
        / 3
    )  # amortized across three tiers
    clues = compute_cost(
        "claude-sonnet-5",
        Usage(input_tokens=4_000, cache_read_input_tokens=8_000, output_tokens=6_000),
        batch=True,
        on=date(2026, 9, 1),
    )
    qa = compute_cost(
        "claude-opus-5",
        Usage(input_tokens=4_000, cache_read_input_tokens=6_000, output_tokens=2_000),
    )
    total = ideation + clues + qa
    assert Decimal("0.10") < total < Decimal("0.30"), total


# ----------------------------------------------------------------------
# Sinks and rollup
# ----------------------------------------------------------------------


def _call(stage: str, model: str, **usage: int) -> LLMCall:
    u = Usage(**usage)
    return LLMCall(stage=stage, model=model, usage=u, cost_usd=compute_cost(model, u))


def test_memory_sink_roundtrip() -> None:
    sink = MemorySink()
    sink.record(_call("theme_ideation", "claude-opus-5", input_tokens=100))
    assert len(list(sink.read_all())) == 1


def test_jsonl_sink_roundtrip(tmp_path: Path) -> None:
    sink = JsonlSink(tmp_path / "nested" / "calls.jsonl")
    sink.record(_call("clue_writing", "claude-sonnet-5", input_tokens=10, output_tokens=20))
    sink.record(_call("clue_writing", "claude-sonnet-5", input_tokens=30, output_tokens=40))

    calls = list(sink.read_all())
    assert len(calls) == 2
    assert calls[0].usage.input_tokens == 10
    assert calls[1].usage.output_tokens == 40


def test_jsonl_sink_on_missing_file_is_empty(tmp_path: Path) -> None:
    assert list(JsonlSink(tmp_path / "absent.jsonl").read_all()) == []


def test_rollup_groups_by_stage_and_model() -> None:
    calls = [
        _call("theme_ideation", "claude-opus-5", input_tokens=1000, output_tokens=500),
        _call("theme_ideation", "claude-opus-5", input_tokens=2000, output_tokens=500),
        _call("clue_writing", "claude-sonnet-5", input_tokens=5000, output_tokens=3000),
    ]
    rows = rollup(iter(calls))
    assert len(rows) == 2
    by_stage = {r.stage: r for r in rows}
    assert by_stage["theme_ideation"].calls == 2
    assert by_stage["theme_ideation"].input_tokens == 3000
    assert rows[0].cost_usd >= rows[1].cost_usd  # sorted by spend


def test_rollup_reports_cache_hit_ratio() -> None:
    """A ratio trending to zero is the canary for volatile content above a breakpoint."""
    call = _call(
        "theme_ideation", "claude-opus-5", input_tokens=2_000, cache_read_input_tokens=8_000
    )
    row = rollup(iter([call]))[0]
    assert row.cache_hit_ratio == pytest.approx(0.8)


def test_rollup_respects_time_window() -> None:
    old = _call("s", "claude-opus-5", input_tokens=1)
    old.ts = datetime.now(UTC) - timedelta(days=30)
    new = _call("s", "claude-opus-5", input_tokens=1)
    rows = rollup(iter([old, new]), since=datetime.now(UTC) - timedelta(days=7))
    assert rows[0].calls == 1


def test_make_sink_rejects_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown cost sink"):
        make_sink("carrier-pigeon", tmp_path / "x.jsonl")
