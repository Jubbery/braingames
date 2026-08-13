"""Publishing knowledge documents as Claude Code skills.

`.claude/skills/` is generated output. The point of these tests is that it
cannot silently diverge from `knowledge/` — that divergence is exactly the
problem the single-substrate design exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from helpers import write_pattern

from braingames_kb.loader import KnowledgeBase
from braingames_kb.models import Stage
from braingames_kb.skills import (
    GENERATED_MARKER,
    SKILLS,
    SkillSpec,
    check,
    documents_for,
    render_skill,
    sync,
)

SPEC = SkillSpec(
    name="test-skill",
    description="A skill used by tests to check rendering and sync behaviour.",
    stages=(Stage.THEME_IDEATION,),
)


def test_skill_frontmatter_is_well_formed(simple_kb: KnowledgeBase) -> None:
    text = render_skill(SPEC, documents_for(simple_kb, SPEC), simple_kb.version())
    lines = text.splitlines()
    assert lines[0] == "---"
    assert lines[1] == "name: test-skill"
    assert lines[2].startswith("description: ")
    assert lines[3] == "---"


def test_document_bodies_are_inlined(simple_kb: KnowledgeBase) -> None:
    """A skill that points at files the agent must go and open loses to one that
    just contains the content."""
    text = render_skill(SPEC, documents_for(simple_kb, SPEC), simple_kb.version())
    assert "Always routed." in text
    assert "Tag routed." in text


def test_summaries_appear_as_a_contents_list(simple_kb: KnowledgeBase) -> None:
    text = render_skill(SPEC, documents_for(simple_kb, SPEC), simple_kb.version())
    assert "## Contents" in text
    assert "**patterns/always-on**" in text


def test_only_active_documents_are_published(kb_root: Path) -> None:
    write_pattern(kb_root, "live-one", always=True)
    path = write_pattern(kb_root, "draft-one", always=True)
    path.write_text(path.read_text().replace("status: active", "status: draft"))
    kb = KnowledgeBase.load(kb_root, strict=True)

    refs = [d.ref for d in documents_for(kb, SPEC)]
    assert refs == ["patterns/live-one"]


def test_rendering_is_deterministic(simple_kb: KnowledgeBase) -> None:
    docs = documents_for(simple_kb, SPEC)
    first = render_skill(SPEC, docs, simple_kb.version())
    for _ in range(5):
        assert render_skill(SPEC, docs, simple_kb.version()) == first


def test_generated_files_say_they_are_generated(simple_kb: KnowledgeBase, tmp_path: Path) -> None:
    sync(simple_kb, tmp_path)
    for spec in SKILLS:
        target = tmp_path / spec.name / "SKILL.md"
        assert GENERATED_MARKER in target.read_text()
        assert "edit knowledge/" in target.read_text().lower()


def test_sync_writes_every_skill(simple_kb: KnowledgeBase, tmp_path: Path) -> None:
    result = sync(simple_kb, tmp_path)
    assert len(result.written) == len(SKILLS)
    assert all((tmp_path / s.name / "SKILL.md").exists() for s in SKILLS)


def test_sync_is_idempotent(simple_kb: KnowledgeBase, tmp_path: Path) -> None:
    sync(simple_kb, tmp_path)
    second = sync(simple_kb, tmp_path)
    assert second.written == []
    assert second.changed is False


def test_sync_rewrites_when_a_document_changes(
    simple_kb: KnowledgeBase, kb_root: Path, tmp_path: Path
) -> None:
    sync(simple_kb, tmp_path)
    write_pattern(kb_root, "always-on", body="Rewritten guidance.", always=True)
    updated = KnowledgeBase.load(kb_root, strict=True)

    result = sync(updated, tmp_path)
    assert result.written
    assert "Rewritten guidance." in (tmp_path / "braingames-puzzles" / "SKILL.md").read_text()


def test_check_detects_staleness(simple_kb: KnowledgeBase, kb_root: Path, tmp_path: Path) -> None:
    """CI runs this so a knowledge change that forgets the sync fails the build."""
    sync(simple_kb, tmp_path)
    assert check(simple_kb, tmp_path) == []

    write_pattern(kb_root, "always-on", body="Changed.", always=True)
    updated = KnowledgeBase.load(kb_root, strict=True)
    assert "braingames-puzzles" in check(updated, tmp_path)


def test_check_reports_everything_when_nothing_is_published(
    simple_kb: KnowledgeBase, tmp_path: Path
) -> None:
    assert set(check(simple_kb, tmp_path)) == {s.name for s in SKILLS}


def test_prune_removes_generated_skills_that_no_longer_exist(
    simple_kb: KnowledgeBase, tmp_path: Path
) -> None:
    sync(simple_kb, tmp_path)
    orphan = tmp_path / "braingames-retired" / "SKILL.md"
    orphan.parent.mkdir(parents=True)
    orphan.write_text(f"---\nname: braingames-retired\n---\n{GENERATED_MARKER}\n")

    result = sync(simple_kb, tmp_path)
    assert orphan.parent in result.removed
    assert not orphan.parent.exists()


def test_prune_leaves_hand_written_skills_alone(simple_kb: KnowledgeBase, tmp_path: Path) -> None:
    """A skill somebody wrote by hand is not ours to delete."""
    handmade = tmp_path / "someones-own-skill" / "SKILL.md"
    handmade.parent.mkdir(parents=True)
    handmade.write_text("---\nname: someones-own-skill\ndescription: Hand written.\n---\n")

    sync(simple_kb, tmp_path)
    assert handmade.exists()


def test_empty_skill_says_so_rather_than_rendering_a_stub(
    simple_kb: KnowledgeBase, tmp_path: Path
) -> None:
    result = sync(simple_kb, tmp_path)
    assert "braingames-frontend" in result.empty
    text = (tmp_path / "braingames-frontend" / "SKILL.md").read_text()
    assert "No documents are published" in text
    assert "bg kb new" in text


def test_shipped_skills_are_in_sync(real_kb: KnowledgeBase) -> None:
    """The committed .claude/skills/ must match the committed knowledge/."""
    root = Path(__file__).resolve().parents[3] / ".claude" / "skills"
    assert check(real_kb, root) == [], "Run `bg kb sync-skills` and commit the result."


def test_shipped_skill_descriptions_are_specific(real_kb: KnowledgeBase) -> None:
    """A skill claiming to cover everything gets loaded for everything."""
    for spec in SKILLS:
        assert len(spec.description) > 80, spec.name
        assert "Use when" in spec.description, spec.name
