"""Measure document token counts.

Measured with ``count_tokens`` against the model the documents are actually
sent to. Not estimated: a budget built on a character heuristic is not a
budget, and OpenAI tokenizers (``tiktoken`` and friends) are wrong for Claude
by 15-20% on prose and considerably more on code.

The measured count is stored alongside the body hash it was measured against,
so validation can spot a stale number without another API call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from braingames_core.costs import atomic_write
from braingames_core.llm import LLMClient

from . import frontmatter
from .loader import KnowledgeBase
from .models import Doc


@dataclass
class MeasureResult:
    ref: str
    previous: int | None
    measured: int
    changed: bool


def measure(doc: Doc, client: LLMClient, *, model: str | None = None) -> int:
    return client.count_tokens(doc.body, model=model, stage="kb_token_count")


def update_document(doc: Doc, tokens: int) -> None:
    """Rewrite a document's frontmatter with a fresh token count.

    Only ``tokens`` and ``token_hash`` change; the body is written back
    untouched so the hash stays valid.
    """
    parsed = frontmatter.read(doc.path)
    meta = dict(parsed.meta)
    meta["tokens"] = tokens
    meta["token_hash"] = doc.body_hash
    atomic_write(doc.path, frontmatter.render(meta, parsed.body, doc.path.suffix))


def refresh(
    kb: KnowledgeBase,
    client: LLMClient,
    *,
    only_stale: bool = True,
    model: str | None = None,
) -> list[MeasureResult]:
    """Measure and persist token counts across the knowledge base."""
    results: list[MeasureResult] = []
    for doc in kb.all():
        if only_stale and not doc.tokens_are_stale:
            continue
        counted = measure(doc, client, model=model)
        previous = doc.meta.tokens
        update_document(doc, counted)
        results.append(
            MeasureResult(
                ref=doc.ref,
                previous=previous,
                measured=counted,
                changed=previous != counted,
            )
        )
    return results


def reload(root: Path) -> KnowledgeBase:
    return KnowledgeBase.load(root, strict=False)


__all__ = ["MeasureResult", "measure", "refresh", "reload", "update_document"]
