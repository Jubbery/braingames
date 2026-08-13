"""Prompt assembly — the most cache-sensitive code in the system.

Block order is load-bearing and is the thing most likely to be quietly broken
by a later change (docs/11 §11.5):

    block 0  GLOBAL SPINE   identical for every cohort → one cache entry
                            shared by the entire nightly run
    block 1  ROUTED DOCS    stable per (cohort, tier)
    block 2  INDEX          stable per kb_version, carries the breakpoint
    ─────────── cache breakpoint ───────────
    user turn               everything volatile lives here

Putting anything cohort-specific ahead of the spine would give every cohort its
own cache entry and multiply cache-write cost by the cohort count. Putting
anything run-specific anywhere above the breakpoint would invalidate the whole
prefix on every call.

Neither mistake produces an error at runtime, so ``assemble`` refuses to build
a prompt that contains one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .loader import KnowledgeBase
from .models import Stage
from .router import RouteResult, RoutingContext, route, sort_key
from .volatile import assert_stable

SystemBlock = dict[str, Any]

#: Hard ceiling on the spine. Docs/11 §11.3 puts it at 8k; exceeding it means
#: something belongs in patterns/ and should be routed in on demand instead.
SPINE_CAP = 8_000

INDEX_PREAMBLE = """\
# Knowledge base index

These documents are available but not loaded. Read one with the `read_knowledge`
tool when its description matches what you are about to do. Prefer reading a
template before writing something from scratch."""


class SpineTooLargeError(RuntimeError):
    """The always-loaded spine exceeded its cap."""


@dataclass
class AssembledPrompt:
    blocks: list[SystemBlock] = field(default_factory=list)
    kb_version: str = ""
    spine_refs: list[str] = field(default_factory=list)
    routed: RouteResult | None = None
    indexed_refs: list[str] = field(default_factory=list)

    @property
    def docs_used(self) -> list[str]:
        """Provenance, recorded on every generated artifact."""
        routed = self.routed.refs if self.routed else []
        return self.spine_refs + routed

    @property
    def text(self) -> str:
        return "\n\n".join(str(b["text"]) for b in self.blocks)

    def approx_tokens(self) -> int:
        return len(self.text) // 3


def render_spine(kb: KnowledgeBase, stage: Stage) -> tuple[str, list[str], int]:
    docs = kb.core(stage)
    body = "\n\n".join(d.render() for d in docs)
    cost = sum(d.cost_tokens() for d in docs)
    return body, [d.versioned_ref for d in docs], cost


def render_index(kb: KnowledgeBase, stage: Stage) -> tuple[str, list[str]]:
    """The model's map of what it can reach.

    Only routable documents appear — listing the spine would be noise, since it
    is already in the prompt.
    """
    docs = sorted(kb.routable(stage), key=sort_key)
    if not docs:
        return "", []
    lines = "\n".join(d.index_line() for d in docs)
    return f"{INDEX_PREAMBLE}\n\n{lines}", [d.ref for d in docs]


def assemble(
    kb: KnowledgeBase,
    stage: Stage,
    ctx: RoutingContext,
    *,
    budget: int | None = None,
    include_index: bool = True,
    cache_ttl: str = "1h",
) -> AssembledPrompt:
    """Build the system blocks for one call.

    Raises ``VolatilePromptError`` if any block above the breakpoint contains a
    runtime-varying literal, naming the offending block. That check is the
    enforcement behind P1.3's acceptance test.
    """
    spine_text, spine_refs, spine_cost = render_spine(kb, stage)
    if spine_cost > SPINE_CAP:
        raise SpineTooLargeError(
            f"Spine for {stage.value} is {spine_cost} tokens, over the {SPINE_CAP} cap.\n"
            f"Documents: {', '.join(spine_refs)}\n"
            f"Move the situational parts into patterns/ so they are routed in on demand."
        )

    routed = route(kb, stage, ctx, budget=budget)

    blocks: list[SystemBlock] = []

    if spine_text:
        assert_stable(spine_text, block_index=len(blocks), block_name="global spine")
        blocks.append({"type": "text", "text": spine_text})

    if routed.selected:
        routed_text = "\n\n".join(d.render() for d in routed.selected)
        assert_stable(routed_text, block_index=len(blocks), block_name="routed documents")
        blocks.append({"type": "text", "text": routed_text})

    indexed_refs: list[str] = []
    if include_index:
        index_text, indexed_refs = render_index(kb, stage)
        if index_text:
            assert_stable(index_text, block_index=len(blocks), block_name="index")
            blocks.append({"type": "text", "text": index_text})

    # The breakpoint goes on the last block above it. Everything volatile —
    # the date, the cohort brief's dynamic parts, the actual request — belongs
    # in the user turn, which is below this line.
    if blocks:
        blocks[-1]["cache_control"] = {"type": "ephemeral", "ttl": cache_ttl}

    return AssembledPrompt(
        blocks=blocks,
        kb_version=kb.version(),
        spine_refs=spine_refs,
        routed=routed,
        indexed_refs=indexed_refs,
    )


def fingerprint(prompt: AssembledPrompt) -> str:
    """Stable hash of the assembled blocks. Used by the determinism test."""
    import hashlib

    h = hashlib.sha256()
    for block in prompt.blocks:
        h.update(str(block.get("text", "")).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


__all__ = [
    "SPINE_CAP",
    "AssembledPrompt",
    "SpineTooLargeError",
    "SystemBlock",
    "assemble",
    "fingerprint",
    "render_index",
    "render_spine",
]
