"""The read_knowledge tool — agentic retrieval.

The id arrives from the model, so it is untrusted input. It must resolve
against the loaded knowledge base and never reach the filesystem.
"""

from __future__ import annotations

from braingames_kb.loader import KnowledgeBase
from braingames_kb.models import Stage
from braingames_kb.tools import TOOL_DEF, ReadLog, handle_read_knowledge

STAGE = Stage.THEME_IDEATION


def test_tool_definition_is_strict() -> None:
    assert TOOL_DEF["strict"] is True
    schema = TOOL_DEF["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["doc_id"]


def test_reads_by_ref(simple_kb: KnowledgeBase) -> None:
    result = handle_read_knowledge(simple_kb, "patterns/always-on")
    assert not result.is_error
    assert "Always routed." in result.content
    assert 'ref="patterns/always-on"' in result.content


def test_reads_by_bare_id(simple_kb: KnowledgeBase) -> None:
    assert not handle_read_knowledge(simple_kb, "always-on").is_error


def test_unknown_id_suggests_near_matches(simple_kb: KnowledgeBase) -> None:
    result = handle_read_knowledge(simple_kb, "patterns/always-onn")
    assert result.is_error
    assert "patterns/always-on" in result.content
    assert "Did you mean" in result.content


def test_unknown_id_with_no_near_match_points_at_the_index(simple_kb: KnowledgeBase) -> None:
    result = handle_read_knowledge(simple_kb, "completely-unrelated-thing")
    assert result.is_error
    assert "index" in result.content.lower()


def test_wrong_stage_is_refused_with_the_right_stages(simple_kb: KnowledgeBase) -> None:
    result = handle_read_knowledge(simple_kb, "always-on", stage=Stage.CLUE_WRITING)
    assert result.is_error
    assert "theme_ideation" in result.content


def test_path_traversal_does_not_reach_the_filesystem(simple_kb: KnowledgeBase) -> None:
    """A model-supplied string must never be treated as a path."""
    for attempt in (
        "../../../../etc/passwd",
        "/etc/passwd",
        "core/../../secrets",
        "patterns/always-on/../../../.env",
    ):
        result = handle_read_knowledge(simple_kb, attempt)
        assert result.is_error, attempt
        assert "root:" not in result.content


def test_reads_are_logged(simple_kb: KnowledgeBase) -> None:
    log = ReadLog(run_id="run-under-test")
    handle_read_knowledge(simple_kb, "always-on", stage=STAGE, log=log)
    handle_read_knowledge(simple_kb, "does-not-exist", stage=STAGE, log=log)

    assert log.refs_read == ["patterns/always-on"]
    assert log.misses == ["does-not-exist"]
    assert all(e["run_id"] == "run-under-test" for e in log.entries)


def test_log_is_the_usage_signal_for_review(simple_kb: KnowledgeBase) -> None:
    """Documents nobody reads get retired; the log is how we know."""
    log = ReadLog()
    for _ in range(3):
        handle_read_knowledge(simple_kb, "tag-gated", log=log)
    assert log.refs_read.count("patterns/tag-gated") == 3
