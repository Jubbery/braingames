"""Admission control for knowledge documents.

A knowledge base with no admission control becomes a junk drawer, and a junk
drawer measurably degrades model output. So every candidate document runs
against a frozen golden set with and without it, and the default verdict is
**reject** — a document that improves nothing is not neutral, it costs tokens
and dilutes attention (docs/11 §11.7).

The runner is pluggable because the real one does not exist yet: it arrives in
P3.6 once there is a generation pipeline to measure. ``StubRunner`` keeps the
harness exercisable before then, which is the point of building it in P1.5
rather than waiting.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, Field

from .loader import KnowledgeBase
from .models import Stage
from .router import RoutingContext

Verdict = Literal["promote", "revise", "reject"]


class MetricSpec(BaseModel):
    name: str
    direction: Literal["higher", "lower"] = "higher"
    #: Gates count toward the verdict. Non-gate metrics are reported for
    #: context — token cost, for instance, is expected to rise and should not
    #: by itself block a document that improves quality.
    gate: bool = False
    #: How much movement counts as real rather than noise.
    tolerance: float = 0.0
    fmt: str = ".2f"

    def is_improvement(self, delta: float) -> bool:
        return delta > self.tolerance if self.direction == "higher" else delta < -self.tolerance

    def is_regression(self, delta: float) -> bool:
        return delta < -self.tolerance if self.direction == "higher" else delta > self.tolerance


class Scenario(BaseModel):
    id: str
    ctx: RoutingContext = Field(default_factory=RoutingContext)
    inputs: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class GoldenSet(BaseModel):
    """A frozen set of generation scenarios plus the metrics that judge them."""

    name: str
    stage: Stage
    description: str = ""
    metrics: list[MetricSpec] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> GoldenSet:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def discover(cls, root: Path) -> dict[str, GoldenSet]:
        if not root.is_dir():
            return {}
        return {p.stem: cls.load(p) for p in sorted(root.glob("*.yaml"))}


class Runner(Protocol):
    """Executes one scenario and returns raw metric values.

    The real implementation calls the generation pipeline. Signature is fixed
    now so P3.6 is a drop-in rather than a refactor.
    """

    def __call__(
        self, scenario: Scenario, kb: KnowledgeBase, *, stage: Stage
    ) -> dict[str, float]: ...


class StubRunner:
    """Deterministic fake for exercising the harness without a pipeline.

    Derives stable pseudo-metrics from the assembled prompt so the plumbing —
    scenario loading, with/without KB construction, aggregation, verdict — is
    genuinely tested. It measures nothing about puzzle quality and must never
    be used to justify promoting a document.
    """

    def __init__(self, metrics: Iterable[MetricSpec]) -> None:
        self.metrics = list(metrics)

    def __call__(self, scenario: Scenario, kb: KnowledgeBase, *, stage: Stage) -> dict[str, float]:
        from .assembler import assemble

        prompt = assemble(kb, stage, scenario.ctx)
        n_docs = len(prompt.docs_used)
        size = prompt.approx_tokens()

        out: dict[str, float] = {}
        for spec in self.metrics:
            if "token" in spec.name:
                out[spec.name] = float(size)
            else:
                # More routed context nudges the score up, with diminishing returns.
                out[spec.name] = round(0.60 + 0.08 * min(n_docs, 4), 4)
        return out


@dataclass
class MetricDelta:
    spec: MetricSpec
    without: float
    with_: float

    @property
    def delta(self) -> float:
        return self.with_ - self.without

    @property
    def verdict_symbol(self) -> str:
        if not self.spec.gate:
            return "~"
        if self.spec.is_improvement(self.delta):
            return "✓"
        if self.spec.is_regression(self.delta):
            return "✗"
        return "~"


@dataclass
class EvalReport:
    doc_id: str
    golden_set: str
    stage: Stage
    scenarios: int
    deltas: list[MetricDelta]

    @property
    def improved(self) -> list[MetricDelta]:
        return [d for d in self.deltas if d.spec.gate and d.spec.is_improvement(d.delta)]

    @property
    def regressed(self) -> list[MetricDelta]:
        return [d for d in self.deltas if d.spec.gate and d.spec.is_regression(d.delta)]

    @property
    def verdict(self) -> Verdict:
        if self.improved and not self.regressed:
            return "promote"
        if self.improved and self.regressed:
            return "revise"
        return "reject"

    @property
    def rationale(self) -> str:
        match self.verdict:
            case "promote":
                return f"{len(self.improved)} gate(s) improved, none regressed"
            case "revise":
                return (
                    f"{len(self.improved)} gate(s) improved but "
                    f"{len(self.regressed)} regressed — narrow its triggers or split it"
                )
            case _:
                if self.regressed:
                    return f"{len(self.regressed)} gate(s) regressed"
                return "no gate improved; the document does not earn its tokens"


def without_doc(kb: KnowledgeBase, doc_id: str) -> KnowledgeBase:
    """A view of the knowledge base with one document removed."""
    target = kb.get(doc_id)
    keep = [d for d in kb.all() if target is None or d.id != target.id]
    return KnowledgeBase(keep, kb.defects, kb.root)


def run_eval(
    kb: KnowledgeBase,
    doc_id: str,
    golden: GoldenSet,
    runner: Runner,
) -> EvalReport:
    """Compare generation with and without one document."""
    if kb.get(doc_id) is None:
        raise KeyError(f"No document {doc_id!r} in the knowledge base")
    if not golden.scenarios:
        raise ValueError(f"Golden set {golden.name!r} has no scenarios")

    kb_without = without_doc(kb, doc_id)

    with_runs = [runner(s, kb, stage=golden.stage) for s in golden.scenarios]
    without_runs = [runner(s, kb_without, stage=golden.stage) for s in golden.scenarios]

    deltas: list[MetricDelta] = []
    for spec in golden.metrics:
        deltas.append(
            MetricDelta(
                spec=spec,
                without=_mean(without_runs, spec.name),
                with_=_mean(with_runs, spec.name),
            )
        )

    return EvalReport(
        doc_id=doc_id,
        golden_set=golden.name,
        stage=golden.stage,
        scenarios=len(golden.scenarios),
        deltas=deltas,
    )


def _mean(runs: list[dict[str, float]], key: str) -> float:
    values = [r[key] for r in runs if key in r]
    return round(statistics.fmean(values), 4) if values else 0.0


__all__ = [
    "EvalReport",
    "GoldenSet",
    "MetricDelta",
    "MetricSpec",
    "Runner",
    "Scenario",
    "StubRunner",
    "Verdict",
    "run_eval",
    "without_doc",
]
