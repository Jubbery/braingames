from __future__ import annotations

from pathlib import Path

CORE = """\
---
id: {id}
kind: core
applies_to: [theme_ideation]
summary: A core document used by tests to exercise the spine assembly path.
version: 1
status: active
priority: {priority}
tokens: 100
token_hash: "{token_hash}"
owner: test
---

{body}
"""

PATTERN = """\
---
id: {id}
kind: pattern
applies_to: [theme_ideation]
summary: A routable pattern used by tests to exercise trigger matching and budgeting.
version: 1
status: active
priority: {priority}
required: {required}
tokens: {tokens}
token_hash: "{token_hash}"
triggers:
{triggers}
evidence: eval/test@seed
owner: test
---

{body}
"""


def _hash(body: str) -> str:
    import hashlib

    return hashlib.sha256(body.encode()).hexdigest()


def write_core(root: Path, doc_id: str, body: str = "Spine content.", priority: int = 10) -> Path:
    path = root / "core" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        CORE.format(id=doc_id, priority=priority, body=body, token_hash=_hash(body)),
        encoding="utf-8",
    )
    return path


def write_pattern(
    root: Path,
    doc_id: str,
    *,
    body: str = "Pattern content.",
    priority: int = 100,
    tokens: int = 100,
    required: bool = False,
    always: bool = False,
    tags: list[str] | None = None,
    motif_keywords: list[str] | None = None,
) -> Path:
    lines = []
    if always:
        lines.append("  always: true")
    if tags:
        lines.append("  tags: [" + ", ".join(tags) + "]")
    if motif_keywords:
        lines.append("  motif_keywords: [" + ", ".join(motif_keywords) + "]")
    if not lines:
        lines.append("  always: true")

    path = root / "patterns" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        PATTERN.format(
            id=doc_id,
            priority=priority,
            required=str(required).lower(),
            tokens=tokens,
            body=body,
            token_hash=_hash(body),
            triggers="\n".join(lines),
        ),
        encoding="utf-8",
    )
    return path
