"""Prompt assembly — the cache-sensitive path.

The two tests that matter most in Phase 1 live here (P1.3):

* ``test_assembly_is_byte_identical`` — the same inputs must produce the same
  bytes, or the prompt cache cannot survive.
* ``test_volatile_content_in_spine_is_rejected`` — a timestamp introduced above
  the breakpoint must fail loudly and name the block, because otherwise the
  failure is silent and shows up only as a bill.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from helpers import write_core, write_pattern

from braingames_kb.assembler import (
    SPINE_CAP,
    SpineTooLargeError,
    assemble,
    fingerprint,
    render_index,
)
from braingames_kb.loader import KnowledgeBase
from braingames_kb.models import Stage
from braingames_kb.router import RoutingContext
from braingames_kb.volatile import VolatilePromptError

STAGE = Stage.THEME_IDEATION


def test_block_order_is_spine_then_routed_then_index(simple_kb: KnowledgeBase) -> None:
    ctx = RoutingContext(cohort_tags=["outdoors.hiking"], motif="snow on the ridge")
    prompt = assemble(simple_kb, STAGE, ctx)

    assert len(prompt.blocks) == 3
    assert "Alpha spine." in prompt.blocks[0]["text"]
    assert "Tag routed." in prompt.blocks[1]["text"]
    assert "Knowledge base index" in prompt.blocks[2]["text"]


def test_cache_breakpoint_is_on_the_last_block(simple_kb: KnowledgeBase) -> None:
    prompt = assemble(simple_kb, STAGE, RoutingContext())
    assert "cache_control" in prompt.blocks[-1]
    assert prompt.blocks[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    for block in prompt.blocks[:-1]:
        assert "cache_control" not in block


def test_spine_is_independent_of_context(simple_kb: KnowledgeBase) -> None:
    """Block 0 must be identical across cohorts.

    If it were not, every cohort would get its own cache entry and cache-write
    cost would multiply by the cohort count.
    """
    a = assemble(simple_kb, STAGE, RoutingContext(cohort_tags=["outdoors.hiking"], tier=1))
    b = assemble(simple_kb, STAGE, RoutingContext(cohort_tags=["food.baking"], tier=3))
    assert a.blocks[0]["text"] == b.blocks[0]["text"]


def test_assembly_is_byte_identical(simple_kb: KnowledgeBase) -> None:
    """P1.3 acceptance: same (stage, ctx, kb_version) → same bytes, every time."""
    ctx = RoutingContext(cohort_tags=["outdoors.hiking"], tier=2, motif="dust and snow")
    first = assemble(simple_kb, STAGE, ctx)
    for _ in range(25):
        again = assemble(simple_kb, STAGE, ctx)
        assert fingerprint(again) == fingerprint(first)
        assert again.blocks == first.blocks


def test_assembly_is_stable_under_filesystem_order(kb_root: Path) -> None:
    """Document insertion order must not leak into the prompt.

    A set that happens to iterate in insertion order will eventually reorder
    with no code change and no other failing test, silently destroying the
    cache. The explicit sort in router.sort_key is what prevents that; this
    test is what keeps the sort there.
    """
    names = [f"pattern-{i}" for i in range(8)]
    for name in names:
        write_pattern(kb_root, name, body=f"Body {name}.", always=True)
    write_core(kb_root, "spine-doc", body="Spine.")

    ctx = RoutingContext()
    baseline = fingerprint(assemble(KnowledgeBase.load(kb_root, strict=True), STAGE, ctx))

    for seed in range(5):
        shuffled = names[:]
        random.Random(seed).shuffle(shuffled)
        # Rewrite in a different order; mtimes and directory order change.
        for name in shuffled:
            write_pattern(kb_root, name, body=f"Body {name}.", always=True)
        kb = KnowledgeBase.load(kb_root, strict=True)
        assert fingerprint(assemble(kb, STAGE, ctx)) == baseline


def test_volatile_content_in_spine_is_rejected(kb_root: Path) -> None:
    """P1.3 acceptance: a timestamp above the breakpoint fails, naming the block."""
    write_core(
        kb_root,
        "leaky-doc",
        body="Generated at 2026-08-13T04:15:22 for this run.",
    )
    kb = KnowledgeBase.load(kb_root, strict=True)

    with pytest.raises(VolatilePromptError) as exc:
        assemble(kb, STAGE, RoutingContext())

    message = str(exc.value)
    assert "block 0" in message
    assert "global spine" in message
    assert "2026-08-13T04:15:22" in message


def test_volatile_content_in_routed_block_is_rejected(kb_root: Path) -> None:
    write_core(kb_root, "clean-spine", body="Clean.")
    write_pattern(
        kb_root,
        "leaky-pattern",
        body="run id 4f2a91c0-9d3e-4b1a-8c77-1e2f3a4b5c6d",
        always=True,
    )
    kb = KnowledgeBase.load(kb_root, strict=True)

    with pytest.raises(VolatilePromptError) as exc:
        assemble(kb, STAGE, RoutingContext())
    assert "block 1" in str(exc.value)
    assert "routed documents" in str(exc.value)


def test_bare_dates_are_allowed(kb_root: Path) -> None:
    """Documents legitimately cite dates. Only clock-carrying values are volatile."""
    write_core(kb_root, "dated-doc", body="Superseded on 2026-03-01 by the new rule.")
    kb = KnowledgeBase.load(kb_root, strict=True)
    prompt = assemble(kb, STAGE, RoutingContext())
    assert "2026-03-01" in prompt.blocks[0]["text"]


def test_oversized_spine_is_rejected(kb_root: Path) -> None:
    write_core(kb_root, "huge-doc", body="x " * (SPINE_CAP * 3))
    kb = KnowledgeBase.load(kb_root, strict=True)
    with pytest.raises(SpineTooLargeError) as exc:
        assemble(kb, STAGE, RoutingContext())
    assert "patterns/" in str(exc.value)


def test_docs_used_records_versioned_refs(simple_kb: KnowledgeBase) -> None:
    prompt = assemble(simple_kb, STAGE, RoutingContext(cohort_tags=["outdoors.hiking"]))
    assert "core/alpha-rules@1" in prompt.docs_used
    assert "patterns/tag-gated@1" in prompt.docs_used
    assert all("@" in ref for ref in prompt.docs_used)


def test_index_lists_routable_but_not_spine(simple_kb: KnowledgeBase) -> None:
    text, refs = render_index(simple_kb, STAGE)
    assert "patterns/always-on" in text
    assert "core/alpha-rules" not in text  # already in the prompt; listing it is noise
    assert len(refs) == 3


def test_real_kb_assembles_for_every_stage(real_kb: KnowledgeBase) -> None:
    """The shipped knowledge base must assemble cleanly for all stages."""
    for stage in Stage:
        prompt = assemble(real_kb, stage, RoutingContext(motif="snow and dust", tier=2))
        assert prompt.kb_version.startswith("kb-sha256-")
