"""Document authoring and the decay pass."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from helpers import write_core, write_pattern

from braingames_kb.lifecycle import review, scaffold, usage_from_log
from braingames_kb.loader import KnowledgeBase
from braingames_kb.models import Kind, Stage, Status

# ----------------------------------------------------------------------
# Authoring
# ----------------------------------------------------------------------


def test_scaffold_produces_a_valid_document(kb_root: Path) -> None:
    scaffold(
        kb_root,
        doc_id="ribbon-flow",
        kind=Kind.PATTERN,
        stages=[Stage.THEME_SCENE_GENERATION],
        summary="Use when the motif calls for flowing ribbons of colour across the grid.",
        owner="theming",
        motif_keywords=["ribbon"],
    )
    kb = KnowledgeBase.load(kb_root, strict=True)
    doc = kb.get("ribbon-flow")
    assert doc is not None
    assert doc.meta.kind is Kind.PATTERN
    assert doc.meta.triggers.motif_keywords == ["ribbon"]


def test_new_documents_start_as_drafts(kb_root: Path) -> None:
    """Promotion goes through eval, never through the author's confidence."""
    scaffold(
        kb_root,
        doc_id="unproven",
        kind=Kind.PATTERN,
        stages=[Stage.THEME_IDEATION],
        summary="Use when checking that a scaffolded document is not active on creation.",
        owner="test",
        always=True,
    )
    kb = KnowledgeBase.load(kb_root, strict=True)
    doc = kb.get("unproven")
    assert doc is not None and doc.meta.status is Status.DRAFT
    assert kb.active() == []


def test_scaffold_lands_in_the_directory_matching_its_kind(kb_root: Path) -> None:
    path = scaffold(
        kb_root,
        doc_id="norse-myth",
        kind=Kind.DOMAIN,
        stages=[Stage.THEME_IDEATION],
        summary="Use for cohorts with a strong interest in Norse mythology and sagas.",
        owner="personalization",
        tags=["history.mythology"],
    )
    assert path.parent.name == "domains"


def test_scaffold_inserts_a_trigger_placeholder_rather_than_an_unreachable_doc(
    kb_root: Path,
) -> None:
    """A document with no trigger fails validation, so the scaffold never emits one."""
    scaffold(
        kb_root,
        doc_id="no-trigger-given",
        kind=Kind.PATTERN,
        stages=[Stage.THEME_IDEATION],
        summary="Use when verifying the scaffold refuses to emit an unreachable document.",
        owner="test",
    )
    kb = KnowledgeBase.load(kb_root, strict=True)
    doc = kb.get("no-trigger-given")
    assert doc is not None and doc.meta.triggers.is_reachable


def test_scaffold_can_write_a_code_template(kb_root: Path) -> None:
    path = scaffold(
        kb_root,
        doc_id="scene-starter",
        kind=Kind.TEMPLATE,
        stages=[Stage.THEME_SCENE_GENERATION],
        summary="Use as the starting skeleton for a new canvas-based theme scene.",
        owner="theming",
        always=True,
        suffix=".ts",
    )
    assert path.suffix == ".ts"
    assert path.read_text().startswith("/*---")
    kb = KnowledgeBase.load(kb_root, strict=True)
    doc = kb.get("scene-starter")
    assert doc is not None and doc.lang == "typescript"


def test_scaffold_refuses_to_clobber(kb_root: Path) -> None:
    kwargs = dict(
        doc_id="already-here",
        kind=Kind.PATTERN,
        stages=[Stage.THEME_IDEATION],
        summary="Use when checking that scaffolding twice does not overwrite work.",
        owner="test",
        always=True,
    )
    scaffold(kb_root, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(FileExistsError):
        scaffold(kb_root, **kwargs)  # type: ignore[arg-type]


def test_scaffold_sets_a_future_review_date(kb_root: Path) -> None:
    scaffold(
        kb_root,
        doc_id="dated",
        kind=Kind.CORE,
        stages=[Stage.THEME_IDEATION],
        summary="Use when checking that a scaffolded document carries a future review date.",
        owner="test",
    )
    kb = KnowledgeBase.load(kb_root, strict=True)
    doc = kb.get("dated")
    assert doc is not None and doc.meta.review_by is not None
    assert doc.meta.review_by > datetime.now(UTC).date()


# ----------------------------------------------------------------------
# Decay
# ----------------------------------------------------------------------


def _set(path: Path, key: str, value: str) -> None:
    text = path.read_text()
    if f"{key}:" in text:
        import re

        text = re.sub(rf"^{key}:.*$", f"{key}: {value}", text, count=1, flags=re.M)
    else:
        text = text.replace("status:", f"{key}: {value}\nstatus:", 1)
    path.write_text(text)


def test_overdue_review_is_flagged(kb_root: Path) -> None:
    path = write_core(kb_root, "aging")
    _set(path, "review_by", "2020-01-01")
    kb = KnowledgeBase.load(kb_root, strict=True)

    report = review(kb)
    assert [i.ref for i in report.overdue] == ["core/aging"]
    assert "golden set" in report.overdue[0].action


def test_active_pattern_without_evidence_is_challenged(kb_root: Path) -> None:
    path = write_pattern(kb_root, "unproven-pattern", always=True)
    path.write_text(path.read_text().replace("evidence: eval/test@seed\n", ""))
    kb = KnowledgeBase.load(kb_root, strict=True)

    assert [i.ref for i in review(kb).unevidenced] == ["patterns/unproven-pattern"]


def test_deprecated_documents_become_deletable(kb_root: Path) -> None:
    path = write_pattern(kb_root, "retired", always=True)
    _set(path, "status", "deprecated")
    kb = KnowledgeBase.load(kb_root, strict=True)

    report = review(kb)
    assert [i.ref for i in report.deletable] == ["patterns/retired"]
    # A deprecated document should not also be nagged about tokens or evidence.
    assert report.unevidenced == []


def test_unread_check_is_skipped_without_usage_data(kb_root: Path) -> None:
    """Reporting everything as unread before any runs would train people to ignore this."""
    write_pattern(kb_root, "never-used", always=True)
    kb = KnowledgeBase.load(kb_root, strict=True)

    report = review(kb)
    assert report.unread == []
    assert report.usage_available is False


def test_unread_documents_are_flagged_when_usage_is_known(kb_root: Path) -> None:
    write_pattern(kb_root, "read-often", always=True)
    write_pattern(kb_root, "read-never", always=True)
    kb = KnowledgeBase.load(kb_root, strict=True)

    report = review(kb, usage={"patterns/read-often": 12})
    assert [i.ref for i in report.unread] == ["patterns/read-never"]
    assert report.usage_available is True


def test_core_and_input_handling_are_exempt_from_the_unread_check(kb_root: Path) -> None:
    """They are always-loaded, so they never appear in a read log by design."""
    write_core(kb_root, "spine-doc")
    kb = KnowledgeBase.load(kb_root, strict=True)
    assert review(kb, usage={}).unread == []


def test_oversized_spine_is_reported(kb_root: Path) -> None:
    write_core(kb_root, "enormous", body="x " * 60_000)
    kb = KnowledgeBase.load(kb_root, strict=False)

    report = review(kb)
    assert report.oversized_spines
    _stage, cost, refs = report.oversized_spines[0]
    assert cost > 8_000 and "core/enormous@1" in refs


def test_clean_knowledge_base_reports_clean(kb_root: Path) -> None:
    path = write_core(kb_root, "tidy")
    _set(path, "review_by", "2099-01-01")
    kb = KnowledgeBase.load(kb_root, strict=True)
    assert review(kb).is_clean


# ----------------------------------------------------------------------
# Usage log
# ----------------------------------------------------------------------


def test_usage_log_counts_successful_reads_only(tmp_path: Path) -> None:
    log = tmp_path / "reads.jsonl"
    now = datetime.now(UTC).isoformat()
    log.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"ts": now, "doc_id": "patterns/a", "found": True},
                {"ts": now, "doc_id": "patterns/a", "found": True},
                {"ts": now, "doc_id": "patterns/b", "found": False},
            ]
        )
    )
    assert usage_from_log(log) == {"patterns/a": 2}


def test_usage_log_respects_the_window(tmp_path: Path) -> None:
    log = tmp_path / "reads.jsonl"
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    log.write_text(json.dumps({"ts": old, "doc_id": "patterns/a", "found": True}))
    assert usage_from_log(log, since_days=90) == {}


def test_usage_log_tolerates_corruption(tmp_path: Path) -> None:
    """A half-written line from a killed worker must not break the review."""
    log = tmp_path / "reads.jsonl"
    log.write_text(
        json.dumps({"ts": datetime.now(UTC).isoformat(), "doc_id": "patterns/a", "found": True})
        + "\n{ truncated"
    )
    assert usage_from_log(log) == {"patterns/a": 1}


def test_missing_usage_log_is_empty(tmp_path: Path) -> None:
    assert usage_from_log(tmp_path / "absent.jsonl") == {}
