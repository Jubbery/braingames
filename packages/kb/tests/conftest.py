from __future__ import annotations

from pathlib import Path

import pytest
from helpers import write_core, write_pattern

from braingames_kb.loader import KnowledgeBase


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge"
    for sub in ("core", "patterns", "templates", "input-handling", "domains", "postmortems"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def simple_kb(kb_root: Path) -> KnowledgeBase:
    write_core(kb_root, "alpha-rules", body="Alpha spine.", priority=10)
    write_core(kb_root, "beta-voice", body="Beta spine.", priority=20)
    write_pattern(kb_root, "always-on", body="Always routed.", always=True)
    write_pattern(kb_root, "tag-gated", body="Tag routed.", tags=["outdoors.hiking"])
    write_pattern(kb_root, "motif-gated", body="Motif routed.", motif_keywords=["snow", "dust"])
    return KnowledgeBase.load(kb_root, strict=True)


@pytest.fixture
def real_kb() -> KnowledgeBase:
    """The actual shipped knowledge base."""
    root = Path(__file__).resolve().parents[3] / "knowledge"
    return KnowledgeBase.load(root, strict=False)
