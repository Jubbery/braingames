"""Loader and validation.

A malformed document that loads silently is worse than one that crashes the
worker: it degrades every prompt it touches with no signal. Every check here
exists because its absence would be invisible.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import write_core, write_pattern

from braingames_kb.frontmatter import FrontmatterError, parse, render
from braingames_kb.loader import KnowledgeBase, KnowledgeBaseError
from braingames_kb.models import Kind, Stage, Status


def _defect_fields(kb: KnowledgeBase, severity: str) -> set[str | None]:
    return {d.field for d in kb.defects if d.severity == severity}


def test_loads_and_validates(simple_kb: KnowledgeBase) -> None:
    assert len(simple_kb.all()) == 5
    assert simple_kb.errors == []
    assert simple_kb.version().startswith("kb-sha256-")


def test_id_must_match_filename(kb_root: Path) -> None:
    path = write_core(kb_root, "correct-name")
    path.rename(kb_root / "core" / "different-name.md")
    kb = KnowledgeBase.load(kb_root, strict=False)
    assert "id" in _defect_fields(kb, "error")


def test_kind_must_match_directory(kb_root: Path) -> None:
    body = kb_root / "core" / "misplaced.md"
    write_core(kb_root, "misplaced")
    text = body.read_text()
    (kb_root / "patterns" / "misplaced.md").write_text(text)
    body.unlink()
    kb = KnowledgeBase.load(kb_root, strict=False)
    assert "kind" in _defect_fields(kb, "error")


def test_unreachable_document_is_an_error(kb_root: Path) -> None:
    """A non-core document with no triggers can never be routed in."""
    (kb_root / "patterns" / "orphan.md").write_text(
        "---\n"
        "id: orphan\n"
        "kind: pattern\n"
        "applies_to: [theme_ideation]\n"
        "summary: A pattern with no triggers at all, which can never be selected.\n"
        "status: active\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )
    kb = KnowledgeBase.load(kb_root, strict=False)
    errors = [d for d in kb.errors if d.field == "triggers"]
    assert errors and "unreachable" in errors[0].message.lower()


def test_core_needs_no_triggers(kb_root: Path) -> None:
    write_core(kb_root, "spine-doc")
    kb = KnowledgeBase.load(kb_root, strict=True)
    assert kb.errors == []


def test_duplicate_ids_rejected(kb_root: Path) -> None:
    write_pattern(kb_root, "clash", always=True)
    (kb_root / "templates" / "clash.md").write_text(
        (kb_root / "patterns" / "clash.md").read_text().replace("kind: pattern", "kind: template"),
        encoding="utf-8",
    )
    kb = KnowledgeBase.load(kb_root, strict=False)
    assert any("Duplicate id" in d.message for d in kb.errors)


def test_strict_load_raises(kb_root: Path) -> None:
    (kb_root / "core" / "broken.md").write_text("no frontmatter here", encoding="utf-8")
    with pytest.raises(KnowledgeBaseError) as exc:
        KnowledgeBase.load(kb_root, strict=True)
    assert "failed validation" in str(exc.value)


def test_missing_tokens_is_a_warning_not_an_error(kb_root: Path) -> None:
    (kb_root / "core" / "unmeasured.md").write_text(
        "---\n"
        "id: unmeasured\n"
        "kind: core\n"
        "applies_to: [theme_ideation]\n"
        "summary: A core document whose token count has never been measured.\n"
        "status: active\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )
    kb = KnowledgeBase.load(kb_root, strict=True)  # does not raise
    assert "tokens" in _defect_fields(kb, "warning")


def test_stale_token_count_detected(kb_root: Path) -> None:
    path = write_core(kb_root, "drifted", body="Original body.")
    path.write_text(path.read_text().replace("Original body.", "Edited body."), encoding="utf-8")
    kb = KnowledgeBase.load(kb_root, strict=False)
    doc = kb.get("drifted")
    assert doc is not None and doc.tokens_are_stale
    assert any("stale" in d.message for d in kb.warnings)


def test_lookup_by_id_and_ref(simple_kb: KnowledgeBase) -> None:
    assert simple_kb.get("always-on") is not None
    assert simple_kb.get("patterns/always-on") is not None
    assert simple_kb.get("patterns/always-on@1") is not None
    assert simple_kb.get("nope") is None


def test_kb_version_changes_with_content(kb_root: Path) -> None:
    write_core(kb_root, "versioned", body="First.")
    before = KnowledgeBase.load(kb_root, strict=True).version()
    write_core(kb_root, "versioned", body="Second.")
    after = KnowledgeBase.load(kb_root, strict=True).version()
    assert before != after


def test_draft_documents_are_not_active(kb_root: Path) -> None:
    write_core(kb_root, "live-doc")
    path = write_core(kb_root, "draft-doc")
    path.write_text(path.read_text().replace("status: active", "status: draft"), encoding="utf-8")
    kb = KnowledgeBase.load(kb_root, strict=True)
    assert {d.id for d in kb.active()} == {"live-doc"}
    assert len(kb.all()) == 2


# ----------------------------------------------------------------------
# Frontmatter
# ----------------------------------------------------------------------


def test_markdown_frontmatter_roundtrip() -> None:
    text = render({"id": "x", "kind": "core"}, "Body text.", ".md")
    parsed = parse(text, ".md")
    assert parsed.meta["id"] == "x"
    assert parsed.body == "Body text."
    assert parsed.lang == "markdown"


def test_typescript_frontmatter_keeps_file_valid() -> None:
    """Code templates carry frontmatter in a comment so they stay compilable."""
    text = render({"id": "scene", "kind": "template"}, "export const x = 1;", ".ts")
    assert text.startswith("/*---")
    parsed = parse(text, ".ts")
    assert parsed.meta["id"] == "scene"
    assert parsed.body == "export const x = 1;"
    assert parsed.lang == "typescript"


def test_missing_frontmatter_names_the_problem() -> None:
    with pytest.raises(FrontmatterError) as exc:
        parse("# Just a heading\n", ".md")
    assert "'---' fence" in str(exc.value)


def test_unterminated_frontmatter() -> None:
    with pytest.raises(FrontmatterError, match="Unterminated"):
        parse("---\nid: x\n\nbody without close", ".md")


def test_unsupported_extension() -> None:
    with pytest.raises(FrontmatterError, match="Unsupported"):
        parse("whatever", ".rst")


# ----------------------------------------------------------------------
# The shipped knowledge base
# ----------------------------------------------------------------------


def test_real_kb_has_no_errors(real_kb: KnowledgeBase) -> None:
    assert real_kb.errors == [], "\n".join(str(d) for d in real_kb.errors)


def test_real_kb_covers_input_handling(real_kb: KnowledgeBase) -> None:
    """The user-input playbook must be reachable from profile classification."""
    docs = real_kb.active(Stage.PROFILE_CLASSIFICATION)
    ids = {d.id for d in docs}
    assert {
        "freetext-normalization",
        "ambiguity-resolution",
        "sparse-profile",
        "conflicting-signals",
        "sensitive-topics",
        "injection-defense",
    } <= ids


def test_real_kb_all_active_docs_have_summaries(real_kb: KnowledgeBase) -> None:
    for doc in real_kb.active():
        assert len(doc.meta.summary.strip()) >= 20, doc.ref


def test_real_kb_non_core_docs_are_reachable(real_kb: KnowledgeBase) -> None:
    for doc in real_kb.active():
        if doc.meta.kind is not Kind.CORE:
            assert doc.meta.triggers.is_reachable, doc.ref


def test_real_kb_active_docs_have_owners(real_kb: KnowledgeBase) -> None:
    for doc in real_kb.active():
        assert doc.meta.owner != "unassigned", doc.ref
        assert doc.meta.status is Status.ACTIVE
