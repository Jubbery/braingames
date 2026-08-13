"""Admission control.

The default verdict is reject. A document that improves nothing is not
neutral — it costs tokens and dilutes attention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from braingames_kb.evalkit import (
    GoldenSet,
    MetricSpec,
    Scenario,
    StubRunner,
    run_eval,
    without_doc,
)
from braingames_kb.loader import KnowledgeBase
from braingames_kb.models import Stage
from braingames_kb.router import RoutingContext


def _golden() -> GoldenSet:
    return GoldenSet(
        name="test-set",
        stage=Stage.THEME_IDEATION,
        metrics=[
            MetricSpec(name="quality", direction="higher", gate=True, tolerance=0.01),
            MetricSpec(name="mean_tokens_per_call", direction="lower", gate=False),
        ],
        scenarios=[
            Scenario(id="s1", ctx=RoutingContext(cohort_tags=["outdoors.hiking"])),
            Scenario(id="s2", ctx=RoutingContext(motif="snow")),
        ],
    )


def test_without_doc_removes_exactly_one(simple_kb: KnowledgeBase) -> None:
    reduced = without_doc(simple_kb, "tag-gated")
    assert len(reduced.all()) == len(simple_kb.all()) - 1
    assert reduced.get("tag-gated") is None
    assert reduced.get("always-on") is not None


def test_without_doc_changes_kb_version(simple_kb: KnowledgeBase) -> None:
    assert without_doc(simple_kb, "tag-gated").version() != simple_kb.version()


def test_eval_runs_end_to_end(simple_kb: KnowledgeBase) -> None:
    golden = _golden()
    report = run_eval(simple_kb, "tag-gated", golden, StubRunner(golden.metrics))

    assert report.scenarios == 2
    assert {d.spec.name for d in report.deltas} == {"quality", "mean_tokens_per_call"}
    assert report.verdict in {"promote", "revise", "reject"}


def test_unknown_document_raises(simple_kb: KnowledgeBase) -> None:
    with pytest.raises(KeyError):
        run_eval(simple_kb, "no-such-doc", _golden(), StubRunner([]))


def test_empty_golden_set_raises(simple_kb: KnowledgeBase) -> None:
    empty = GoldenSet(name="empty", stage=Stage.THEME_IDEATION, metrics=[], scenarios=[])
    with pytest.raises(ValueError, match="no scenarios"):
        run_eval(simple_kb, "tag-gated", empty, StubRunner([]))


# ----------------------------------------------------------------------
# Verdict logic
# ----------------------------------------------------------------------


def _report(gate_values: list[tuple[float, float]]):
    from braingames_kb.evalkit import EvalReport, MetricDelta

    deltas = [
        MetricDelta(
            spec=MetricSpec(name=f"m{i}", direction="higher", gate=True, tolerance=0.01),
            without=w,
            with_=v,
        )
        for i, (w, v) in enumerate(gate_values)
    ]
    return EvalReport(
        doc_id="d", golden_set="g", stage=Stage.THEME_IDEATION, scenarios=1, deltas=deltas
    )


def test_promote_when_a_gate_improves_and_none_regress() -> None:
    report = _report([(0.80, 0.90), (0.70, 0.70)])
    assert report.verdict == "promote"


def test_revise_when_improvement_and_regression_coexist() -> None:
    report = _report([(0.80, 0.90), (0.70, 0.50)])
    assert report.verdict == "revise"
    assert "narrow its triggers" in report.rationale


def test_reject_when_nothing_improves() -> None:
    """A neutral document still costs tokens, so neutral is a reject."""
    report = _report([(0.80, 0.80), (0.70, 0.70)])
    assert report.verdict == "reject"
    assert "does not earn its tokens" in report.rationale


def test_reject_on_pure_regression() -> None:
    assert _report([(0.80, 0.60)]).verdict == "reject"


def test_tolerance_absorbs_noise() -> None:
    report = _report([(0.800, 0.805)])
    assert report.verdict == "reject"  # movement below tolerance is not improvement


def test_lower_is_better_direction() -> None:
    spec = MetricSpec(name="tokens", direction="lower", gate=True, tolerance=10)
    assert spec.is_improvement(-50)
    assert spec.is_regression(50)
    assert not spec.is_improvement(-5)


# ----------------------------------------------------------------------
# The shipped golden set
# ----------------------------------------------------------------------


def test_shipped_golden_set_loads() -> None:
    path = Path(__file__).resolve().parents[3] / "eval" / "golden" / "theme-scenes.yaml"
    golden = GoldenSet.load(path)
    assert golden.stage is Stage.THEME_SCENE_GENERATION
    assert len(golden.scenarios) >= 5
    assert any(m.gate for m in golden.metrics)


def test_shipped_golden_set_runs_against_real_kb(real_kb: KnowledgeBase) -> None:
    path = Path(__file__).resolve().parents[3] / "eval" / "golden" / "theme-scenes.yaml"
    golden = GoldenSet.load(path)
    report = run_eval(real_kb, "scene-particle-field", golden, StubRunner(golden.metrics))
    assert report.scenarios == len(golden.scenarios)
