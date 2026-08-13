"""Deterministic routing.

Determinism is a correctness requirement here, not a style preference: a
routing step that returns a different set — or the same set in a different
order — silently invalidates the prompt cache and makes bad runs
irreproducible.
"""

from __future__ import annotations

import random

import pytest
from helpers import write_pattern

from braingames_kb.loader import KnowledgeBase
from braingames_kb.models import Stage
from braingames_kb.router import (
    RequiredDocsOverflowError,
    RoutingContext,
    matches,
    route,
    sort_key,
)

STAGE = Stage.THEME_IDEATION


def test_always_trigger_matches_empty_context(simple_kb: KnowledgeBase) -> None:
    result = route(simple_kb, STAGE, RoutingContext())
    assert [d.id for d in result.selected] == ["always-on"]


def test_tag_trigger(simple_kb: KnowledgeBase) -> None:
    result = route(simple_kb, STAGE, RoutingContext(cohort_tags=["outdoors.hiking"]))
    assert "tag-gated" in {d.id for d in result.selected}


def test_tag_matching_is_case_insensitive(simple_kb: KnowledgeBase) -> None:
    result = route(simple_kb, STAGE, RoutingContext(cohort_tags=["OUTDOORS.Hiking"]))
    assert "tag-gated" in {d.id for d in result.selected}


def test_motif_trigger_matches_on_word_not_substring(simple_kb: KnowledgeBase) -> None:
    hit = route(simple_kb, STAGE, RoutingContext(motif="fresh snow on the ridge"))
    assert "motif-gated" in {d.id for d in hit.selected}

    # "snowboarding" contains "snow" but is a different word; tokenized
    # matching keeps the pattern out rather than firing on a substring.
    miss = route(simple_kb, STAGE, RoutingContext(motif="snowboarding championship"))
    assert "motif-gated" not in {d.id for d in miss.selected}


def test_non_matching_context_selects_only_always(simple_kb: KnowledgeBase) -> None:
    result = route(simple_kb, STAGE, RoutingContext(cohort_tags=["food.baking"], motif="bread"))
    assert {d.id for d in result.selected} == {"always-on"}


def test_match_reason_is_reported(simple_kb: KnowledgeBase) -> None:
    doc = simple_kb.get("motif-gated")
    assert doc is not None
    assert matches(doc, RoutingContext(motif="a dust storm")) == "motif:dust"
    assert matches(doc, RoutingContext(motif="a bakery")) is None


def test_routing_is_deterministic_across_shuffles(kb_root) -> None:
    names = [f"p-{i}" for i in range(10)]
    for name in names:
        write_pattern(kb_root, name, always=True, priority=50)

    ctx = RoutingContext()
    baseline = [d.ref for d in route(KnowledgeBase.load(kb_root, strict=True), STAGE, ctx).selected]

    for seed in range(10):
        order = names[:]
        random.Random(seed).shuffle(order)
        for name in order:
            write_pattern(kb_root, name, always=True, priority=50)
        kb = KnowledgeBase.load(kb_root, strict=True)
        assert [d.ref for d in route(kb, STAGE, ctx).selected] == baseline


def test_priority_orders_before_id(kb_root) -> None:
    write_pattern(kb_root, "zzz-first", always=True, priority=10)
    write_pattern(kb_root, "aaa-second", always=True, priority=90)
    kb = KnowledgeBase.load(kb_root, strict=True)
    assert [d.id for d in route(kb, STAGE, RoutingContext()).selected] == [
        "zzz-first",
        "aaa-second",
    ]


def test_equal_priority_breaks_on_ref(kb_root) -> None:
    write_pattern(kb_root, "bravo", always=True, priority=50)
    write_pattern(kb_root, "alpha", always=True, priority=50)
    kb = KnowledgeBase.load(kb_root, strict=True)
    assert [d.id for d in route(kb, STAGE, RoutingContext()).selected] == ["alpha", "bravo"]


def test_budget_drops_and_records(kb_root) -> None:
    write_pattern(kb_root, "cheap-doc", always=True, priority=10, tokens=100)
    write_pattern(kb_root, "pricey-doc", always=True, priority=20, tokens=5000)
    kb = KnowledgeBase.load(kb_root, strict=True)

    result = route(kb, STAGE, RoutingContext(), budget=1000)
    assert [d.id for d in result.selected] == ["cheap-doc"]
    assert len(result.dropped) == 1
    dropped, why = result.dropped[0]
    assert dropped.id == "pricey-doc"
    assert "budget" in why


def test_unmeasured_tokens_use_pessimistic_bound(kb_root) -> None:
    """An unmeasured document must never silently overrun a budget."""
    (kb_root / "patterns" / "unmeasured.md").write_text(
        "---\n"
        "id: unmeasured\n"
        "kind: pattern\n"
        "applies_to: [theme_ideation]\n"
        "summary: A pattern with no measured token count, to check budget fallback.\n"
        "status: active\n"
        "triggers:\n  always: true\n"
        "---\n\n" + ("word " * 900),
        encoding="utf-8",
    )
    kb = KnowledgeBase.load(kb_root, strict=False)
    doc = kb.get("unmeasured")
    assert doc is not None
    assert doc.cost_tokens() > 1000  # bounded by characters, not assumed small


def test_implausible_token_count_is_ignored_for_budgeting(kb_root) -> None:
    """A declared count that cannot be true must not fool the budget.

    `token_hash` catches an edited body. It cannot catch a hand-edited count,
    or one measured against the wrong model — so budgeting sanity-checks the
    number against body length and falls back when it is impossible.
    """
    write_pattern(kb_root, "liar", body="word " * 4000, always=True, tokens=10)
    kb = KnowledgeBase.load(kb_root, strict=False)

    doc = kb.get("liar")
    assert doc is not None
    assert doc.tokens_are_implausible
    assert doc.cost_tokens() > 1000
    assert any("plausible ratio" in d.message for d in kb.warnings)


def test_sort_key_is_total(simple_kb: KnowledgeBase) -> None:
    docs = simple_kb.all()
    keys = [sort_key(d) for d in docs]
    assert len(set(keys)) == len(keys)


def test_deprecated_documents_are_not_routed(kb_root) -> None:
    path = write_pattern(kb_root, "retired", always=True)
    path.write_text(
        path.read_text().replace("status: active", "status: deprecated"), encoding="utf-8"
    )
    kb = KnowledgeBase.load(kb_root, strict=True)
    assert route(kb, STAGE, RoutingContext()).selected == []


def test_stage_isolation(simple_kb: KnowledgeBase) -> None:
    """Documents only route into the stages they declare."""
    assert route(simple_kb, Stage.CLUE_WRITING, RoutingContext()).selected == []


def test_required_documents_survive_a_tight_budget(kb_root) -> None:
    """Budgeting is a quality tradeoff for most documents, a safety one for a few.

    Priority ordering alone is not enough: a required document with a late
    priority would be dropped by a greedy fill, and for the injection-defense
    playbook that is not an acceptable outcome.
    """
    write_pattern(kb_root, "aaa-optional", always=True, priority=1, tokens=900)
    write_pattern(kb_root, "zzz-required", always=True, priority=999, tokens=900, required=True)
    kb = KnowledgeBase.load(kb_root, strict=True)

    result = route(kb, STAGE, RoutingContext(), budget=1000)
    assert [d.id for d in result.selected] == ["zzz-required"]
    assert [d.id for d, _ in result.dropped] == ["aaa-optional"]


def test_required_overflow_raises_rather_than_dropping(kb_root) -> None:
    write_pattern(kb_root, "req-one", always=True, tokens=800, required=True)
    write_pattern(kb_root, "req-two", always=True, tokens=800, required=True)
    kb = KnowledgeBase.load(kb_root, strict=True)

    with pytest.raises(RequiredDocsOverflowError) as exc:
        route(kb, STAGE, RoutingContext(), budget=1000)

    message = str(exc.value)
    assert "patterns/req-one" in message and "patterns/req-two" in message
    assert "STAGE_BUDGETS" in message


def test_selection_order_is_independent_of_the_required_split(kb_root) -> None:
    """The required/optional partition must not leak into the emitted order."""
    write_pattern(kb_root, "alpha", always=True, priority=10, tokens=100)
    write_pattern(kb_root, "bravo", always=True, priority=20, tokens=100, required=True)
    write_pattern(kb_root, "charlie", always=True, priority=30, tokens=100)
    kb = KnowledgeBase.load(kb_root, strict=True)

    assert [d.id for d in route(kb, STAGE, RoutingContext()).selected] == [
        "alpha",
        "bravo",
        "charlie",
    ]


def test_shipped_safety_docs_are_required(real_kb: KnowledgeBase) -> None:
    for doc_id in ("injection-defense", "sensitive-topics"):
        doc = real_kb.get(doc_id)
        assert doc is not None and doc.meta.required, doc_id


def test_shipped_input_handling_playbook_fits_its_budget(real_kb: KnowledgeBase) -> None:
    """Every always-on input-handling document must actually reach the model."""
    result = route(real_kb, Stage.PROFILE_CLASSIFICATION, RoutingContext())
    assert result.dropped == [], [d.ref for d, _ in result.dropped]
    assert len(result.selected) == 6
