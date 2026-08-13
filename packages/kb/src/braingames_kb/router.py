"""Deterministic document routing.

Rule-based, not semantic. Doing vector search here would buy nothing — the
routing keys (cohort tags, tier, motif, stage) are already structured — and it
would cost the three properties that matter (docs/11 §11.4):

1. **Cache survival.** The assembled block must be byte-identical across calls
   for the same cohort and tier. A retrieval step that returns a different set,
   or the same set in a different order, silently costs ~10x on input.
2. **Reproducibility.** A bad puzzle can be regenerated with the exact
   knowledge set that produced it.
3. **It's free.** No embedding call, no latency, no retrieval failure mode.

The agentic path — where the model chooses documents itself — is
``tools.read_knowledge``, and it exists only for open-ended stages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .loader import KnowledgeBase
from .models import Doc, Stage

#: Token ceiling for routed (non-spine) documents, per stage. The spine is
#: budgeted separately and much harder — see assembler.SPINE_CAP.
STAGE_BUDGETS: dict[Stage, int] = {
    Stage.THEME_IDEATION: 6_000,
    Stage.CLUE_WRITING: 4_000,
    Stage.EDITORIAL_QA: 4_000,
    Stage.THEME_SCENE_GENERATION: 12_000,
    # The whole input-handling playbook is always-on for this stage; the
    # budget has to hold all of it, not an arbitrary round number.
    Stage.PROFILE_CLASSIFICATION: 8_000,
    Stage.CODEGEN_BACKEND: 8_000,
    Stage.CODEGEN_FRONTEND: 8_000,
    Stage.CODEGEN_REVIEW: 6_000,
}

_WORD = re.compile(r"[a-z0-9]+")


def sort_key(doc: Doc) -> tuple[int, str]:
    """Total order over documents.

    Priority first, then ref. The tiebreak is not cosmetic: a set that iterates
    in insertion order will eventually reorder and destroy the prompt cache with
    no code change and no failing test.
    """
    return (doc.meta.priority, doc.ref)


class RoutingContext(BaseModel):
    """Everything routing is allowed to depend on.

    Deliberately small and free of runtime identifiers. Note there is no run id
    and no timestamp here — if routing could depend on those, the cache could
    not survive, so the type makes that impossible rather than merely discouraged.
    """

    cohort_tags: list[str] = Field(default_factory=list)
    tier: int | None = None
    motif: str | None = None
    cohort_id: str | None = None

    def motif_tokens(self) -> set[str]:
        return set(_WORD.findall((self.motif or "").lower()))

    def normalized_tags(self) -> set[str]:
        return {t.strip().lower() for t in self.cohort_tags if t.strip()}


class RequiredDocsOverflowError(RuntimeError):
    """Documents marked ``required`` do not fit the stage budget.

    Raised rather than silently dropping one, because the whole point of the
    flag is that dropping it is not an acceptable outcome. Either raise the
    budget or shorten the documents.
    """


@dataclass
class RouteResult:
    """What routing chose, and what it had to leave out."""

    selected: list[Doc] = field(default_factory=list)
    dropped: list[tuple[Doc, str]] = field(default_factory=list)
    budget: int = 0
    tokens_used: int = 0

    @property
    def refs(self) -> list[str]:
        return [d.versioned_ref for d in self.selected]

    @property
    def headroom(self) -> int:
        return self.budget - self.tokens_used


def matches(doc: Doc, ctx: RoutingContext) -> str | None:
    """Why this document applies, or None. The reason is kept for debugging."""
    t = doc.meta.triggers
    if t.always:
        return "always"

    if t.tags:
        hit = sorted(set(t.tags) & ctx.normalized_tags())
        if hit:
            return f"tags:{','.join(hit)}"

    if t.tiers and ctx.tier is not None and ctx.tier in t.tiers:
        return f"tier:{ctx.tier}"

    if t.motif_keywords:
        hit = sorted(set(t.motif_keywords) & ctx.motif_tokens())
        if hit:
            return f"motif:{','.join(hit)}"

    return None


def route(
    kb: KnowledgeBase,
    stage: Stage,
    ctx: RoutingContext,
    *,
    budget: int | None = None,
) -> RouteResult:
    """Select the non-spine documents for one call.

    Greedy fill in priority order. A document that does not fit is dropped and
    recorded rather than silently omitted — a budget overrun should be visible
    in the run trace, not inferred from a puzzle that came out wrong.
    """
    limit = budget if budget is not None else STAGE_BUDGETS.get(stage, 4_000)
    result = RouteResult(budget=limit)

    candidates: list[tuple[Doc, str]] = []
    for doc in kb.routable(stage):
        reason = matches(doc, ctx)
        if reason is not None:
            candidates.append((doc, reason))

    candidates.sort(key=lambda pair: sort_key(pair[0]))

    # Required documents are admitted first and unconditionally. Their cost is
    # charged against the budget so the optional set sees true headroom.
    required = [doc for doc, _ in candidates if doc.meta.required]
    required_cost = sum(doc.cost_tokens() for doc in required)
    if required_cost > limit:
        listing = ", ".join(f"{d.ref} ({d.cost_tokens()})" for d in required)
        raise RequiredDocsOverflowError(
            f"Required documents for {stage.value} need {required_cost} tokens but the "
            f"budget is {limit}.\n  {listing}\n"
            f"Raise STAGE_BUDGETS[{stage.value}] or shorten these documents. Dropping one "
            f"is not an option — that is what `required: true` means."
        )

    result.selected.extend(required)
    result.tokens_used = required_cost

    for doc, _reason in candidates:
        if doc.meta.required:
            continue
        cost = doc.cost_tokens()
        if result.tokens_used + cost <= limit:
            result.selected.append(doc)
            result.tokens_used += cost
        else:
            result.dropped.append((doc, f"budget: needs {cost}, {result.headroom} left"))

    # Keep the emitted order stable and independent of the required/optional
    # split, so the assembled prompt bytes do not depend on that partition.
    result.selected.sort(key=sort_key)
    return result


def explain(kb: KnowledgeBase, stage: Stage, ctx: RoutingContext) -> list[tuple[str, str]]:
    """(ref, reason) for everything that matched. Powers `bg kb route`."""
    out: list[tuple[str, str]] = []
    for doc in sorted(kb.routable(stage), key=sort_key):
        reason = matches(doc, ctx)
        out.append((doc.ref, reason or "—"))
    return out


__all__ = [
    "STAGE_BUDGETS",
    "RequiredDocsOverflowError",
    "RouteResult",
    "RoutingContext",
    "explain",
    "matches",
    "route",
    "sort_key",
]
