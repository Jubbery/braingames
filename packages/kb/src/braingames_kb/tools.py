"""The ``read_knowledge`` tool — agentic retrieval for open-ended stages.

Deterministic routing (``router.route``) is right for the nightly pipeline,
where the needed documents are a function of cohort, tier, and stage. It is
wrong for theme-code generation, where which patterns matter depends on design
decisions the model has not made yet when the prompt is assembled. That stage
gets the index and this tool instead.

Read logs are the usage signal behind the review cycle in docs/11 §11.7:
documents nobody reads get retired; documents read on every run get promoted
into the spine.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .loader import KnowledgeBase
from .models import Stage

TOOL_NAME = "read_knowledge"

TOOL_DEF: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Read a knowledge base document in full. The index in your system prompt "
        "lists what is available with a one-line description of when to use each. "
        "Call this when a listed document is relevant to what you are about to do. "
        "Prefer reading a template before writing something from scratch, and "
        "prefer reading a pattern before solving a problem it covers. "
        "Returns the document body verbatim."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": (
                    "The document reference exactly as it appears in the index, "
                    "e.g. 'patterns/scene-particle-field'."
                ),
            }
        },
        "required": ["doc_id"],
        "additionalProperties": False,
    },
    "strict": True,
}


@dataclass
class ReadLog:
    """Records which documents an agent actually read, per run."""

    run_id: str | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, doc_id: str, *, found: bool, stage: Stage | None = None) -> None:
        self.entries.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "run_id": self.run_id,
                "stage": stage.value if stage else None,
                "doc_id": doc_id,
                "found": found,
            }
        )

    @property
    def refs_read(self) -> list[str]:
        return [e["doc_id"] for e in self.entries if e["found"]]

    @property
    def misses(self) -> list[str]:
        return [e["doc_id"] for e in self.entries if not e["found"]]


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False


def handle_read_knowledge(
    kb: KnowledgeBase,
    doc_id: str,
    *,
    stage: Stage | None = None,
    log: ReadLog | None = None,
) -> ToolResult:
    """Serve one document.

    The id is resolved against the loaded knowledge base, never trusted as a
    path — a model-supplied string must not be able to reach the filesystem.
    An unknown id returns near matches rather than failing the turn, because a
    near miss ("scene-particles" for "scene-particle-field") is the common case
    and is recoverable in one more tool call.
    """
    doc = kb.get(doc_id)

    if doc is None:
        available = [d.ref for d in kb.all()]
        tail = doc_id.split("/")[-1]
        close = difflib.get_close_matches(
            tail, [r.split("/")[-1] for r in available], n=3, cutoff=0.5
        )
        suggestions = [r for r in available if r.split("/")[-1] in close]

        if log:
            log.record(doc_id, found=False, stage=stage)

        hint = (
            f" Did you mean: {', '.join(suggestions)}?"
            if suggestions
            else " Use the index in your system prompt for exact references."
        )
        return ToolResult(content=f"No document with reference {doc_id!r}.{hint}", is_error=True)

    if stage is not None and stage not in doc.meta.applies_to:
        if log:
            log.record(doc.ref, found=False, stage=stage)
        return ToolResult(
            content=(
                f"Document {doc.ref!r} exists but does not apply to the {stage.value} stage. "
                f"It applies to: {', '.join(s.value for s in doc.meta.applies_to)}."
            ),
            is_error=True,
        )

    if log:
        log.record(doc.ref, found=True, stage=stage)

    return ToolResult(content=doc.render())


__all__ = ["TOOL_DEF", "TOOL_NAME", "ReadLog", "ToolResult", "handle_read_knowledge"]
