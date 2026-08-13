"""Document lifecycle: authoring and decay (docs/11-agent-knowledge-base.md §11.7).

Two operations that sound like process and are actually tooling.

**Authoring.** If adding a document means remembering nine frontmatter fields
and which directory maps to which kind, people will paste an existing one and
edit it, and the copied `evidence` and `review_by` will be wrong. Scaffolding
makes the correct thing the easy thing.

**Decay.** A knowledge base that only grows becomes noise, and noise degrades
model output. The decay pass is the documented remedy, and it is also the thing
nobody does unprompted — because nothing breaks when you skip it, output just
quietly gets worse. So it needs a command that lists exactly what to look at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import frontmatter
from .loader import KnowledgeBase
from .models import KIND_DIRS, Doc, Kind, Stage, Status

#: How long a new document gets before its first scheduled review.
DEFAULT_REVIEW_MONTHS = 6

#: A deprecated document is deleted after this long — it stays briefly so a
#: rollback has something to roll back to.
DEPRECATION_GRACE_DAYS = 90


# ----------------------------------------------------------------------
# Authoring
# ----------------------------------------------------------------------

BODY_TEMPLATES: dict[Kind, str] = {
    Kind.CORE: """\
# {title}

<!-- Core documents are always loaded for their stages, so every sentence is
     paid for on every call. Keep it to rules that always apply. Anything
     situational belongs in patterns/ where it is routed in on demand. -->

## The rule

## Why

## Worked example
""",
    Kind.PATTERN: """\
# {title}

<!-- Lead with the failure this pattern prevents. A pattern that only describes
     the happy path does not change behaviour. -->

## The problem

## The approach

```typescript
```

## Budget / cost notes
""",
    Kind.TEMPLATE: """\
# {title}

<!-- Templates are starting points, not illustrations. The code below must be
     complete and correct as written — someone will paste it. -->

## What this already gets right

## The template

```typescript
```
""",
    Kind.INPUT_HANDLING: """\
# {title}

<!-- Player input is untrusted and messy. Say what happens, including for the
     inputs nobody planned for. -->

## The task

## Rules

## Worked examples

| Input | Handling | Note |
|---|---|---|

## What not to do
""",
    Kind.DOMAIN: """\
# {title}

## Vocabulary this cohort finds fair

## Theme seeds

## What to avoid
""",
    Kind.POSTMORTEM: """\
# {title}

## What happened

## Why

## The rule that came out of it

<!-- A postmortem is a staging area. Once the rule is written into a pattern or
     a core document, deprecate this. -->
""",
}


def scaffold(
    root: Path,
    *,
    doc_id: str,
    kind: Kind,
    stages: list[Stage],
    summary: str,
    owner: str,
    title: str | None = None,
    always: bool = False,
    tags: list[str] | None = None,
    motif_keywords: list[str] | None = None,
    tiers: list[int] | None = None,
    required: bool = False,
    suffix: str = ".md",
) -> Path:
    """Write a new draft document. Returns the path.

    New documents are always ``status: draft`` — promotion to active goes
    through ``bg kb eval``, never through the author's own judgement.
    """
    path = root / KIND_DIRS[kind] / f"{doc_id}{suffix}"
    if path.exists():
        raise FileExistsError(f"{path} already exists")

    triggers: dict[str, object] = {}
    if always:
        triggers["always"] = True
    if tags:
        triggers["tags"] = sorted(tags)
    if motif_keywords:
        triggers["motif_keywords"] = sorted(motif_keywords)
    if tiers:
        triggers["tiers"] = sorted(tiers)
    if not triggers and kind is not Kind.CORE:
        # Better a placeholder the author must fill than a document that
        # validates but can never be routed in.
        triggers["tags"] = ["TODO-add-a-trigger"]

    meta: dict[str, object] = {
        "id": doc_id,
        "kind": kind.value,
        "applies_to": [s.value for s in stages],
        "summary": summary,
        "version": 1,
        "status": Status.DRAFT.value,
    }
    if triggers:
        meta["triggers"] = triggers
    if required:
        meta["required"] = True
    meta["review_by"] = _months_ahead(DEFAULT_REVIEW_MONTHS).isoformat()
    meta["owner"] = owner

    body = BODY_TEMPLATES[kind].format(title=title or doc_id.replace("-", " ").title())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter.render(meta, body, suffix), encoding="utf-8")
    return path


def _months_ahead(months: int) -> date:
    today = datetime.now(UTC).date()
    month = today.month - 1 + months
    year = today.year + month // 12
    return date(year, month % 12 + 1, min(today.day, 28))


# ----------------------------------------------------------------------
# Decay
# ----------------------------------------------------------------------


@dataclass
class ReviewItem:
    doc: Doc
    reason: str
    action: str

    @property
    def ref(self) -> str:
        return self.doc.ref


@dataclass
class ReviewReport:
    overdue: list[ReviewItem] = field(default_factory=list)
    unevidenced: list[ReviewItem] = field(default_factory=list)
    unmeasured: list[ReviewItem] = field(default_factory=list)
    unread: list[ReviewItem] = field(default_factory=list)
    stale_drafts: list[ReviewItem] = field(default_factory=list)
    deletable: list[ReviewItem] = field(default_factory=list)
    oversized_spines: list[tuple[Stage, int, list[str]]] = field(default_factory=list)
    usage_available: bool = False

    @property
    def all_items(self) -> list[ReviewItem]:
        return [
            *self.overdue,
            *self.unevidenced,
            *self.unmeasured,
            *self.unread,
            *self.stale_drafts,
            *self.deletable,
        ]

    @property
    def is_clean(self) -> bool:
        return not self.all_items and not self.oversized_spines


def review(
    kb: KnowledgeBase,
    *,
    usage: dict[str, int] | None = None,
    draft_stale_days: int = 60,
) -> ReviewReport:
    """The decay pass.

    ``usage`` maps document ref to read count, from ``read_knowledge`` logs. When
    absent, the unread check is skipped rather than guessed at — reporting every
    document as unread because no runs have happened yet would train people to
    ignore the output.
    """
    from .assembler import SPINE_CAP, render_spine

    today = datetime.now(UTC).date()
    report = ReviewReport(usage_available=usage is not None)

    for doc in kb.all():
        meta = doc.meta

        if meta.status is Status.DEPRECATED:
            report.deletable.append(
                ReviewItem(
                    doc,
                    "deprecated",
                    f"Delete after {DEPRECATION_GRACE_DAYS} days if nothing has needed a rollback.",
                )
            )
            continue

        if meta.status is Status.DRAFT:
            age = _age_days(doc)
            if age is not None and age > draft_stale_days:
                report.stale_drafts.append(
                    ReviewItem(
                        doc,
                        f"draft for {age} days",
                        "Run `bg kb eval` and promote it, or delete it. A permanent draft is clutter.",
                    )
                )
            continue

        if meta.review_by and meta.review_by < today:
            days = (today - meta.review_by).days
            report.overdue.append(
                ReviewItem(
                    doc,
                    f"review {days} days overdue",
                    "Re-run the golden set. Models improve; guidance written to work around an "
                    "older model's failure mode is the highest-value thing to delete.",
                )
            )

        if meta.kind in (Kind.PATTERN, Kind.TEMPLATE) and not meta.evidence:
            report.unevidenced.append(
                ReviewItem(
                    doc,
                    "no evidence",
                    "Point `evidence` at the eval result that justified it, or challenge it.",
                )
            )

        if doc.tokens_are_stale or doc.tokens_are_implausible:
            why = "never measured" if meta.tokens is None else "stale or implausible"
            report.unmeasured.append(ReviewItem(doc, f"tokens {why}", "Run `bg kb tokens`."))

        # Core and input-handling documents are always loaded, so they never
        # appear in a read log and an unread flag would be meaningless.
        routable_kind = meta.kind not in (Kind.CORE, Kind.INPUT_HANDLING)
        if usage is not None and routable_kind and usage.get(doc.ref, 0) == 0:
            report.unread.append(
                ReviewItem(
                    doc,
                    "never read in the window",
                    "Justify it or deprecate it. An unread document is tokens in the index "
                    "for nothing.",
                )
            )

    for stage in Stage:
        _, refs, cost = render_spine(kb, stage)
        if cost > SPINE_CAP:
            report.oversized_spines.append((stage, cost, refs))

    return report


def _age_days(doc: Doc) -> int | None:
    try:
        mtime = doc.path.stat().st_mtime
    except OSError:
        return None
    return (datetime.now(UTC) - datetime.fromtimestamp(mtime, UTC)).days


def usage_from_log(path: Path, *, since_days: int = 90) -> dict[str, int]:
    """Read counts from a ``read_knowledge`` log written by generation runs."""
    import json

    counts: dict[str, int] = {}
    if not path.exists():
        return counts

    cutoff = datetime.now(UTC) - timedelta(days=since_days)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not entry.get("found"):
                continue
            ts = entry.get("ts")
            if ts:
                try:
                    if datetime.fromisoformat(ts) < cutoff:
                        continue
                except ValueError:
                    pass
            ref = entry.get("doc_id")
            if ref:
                counts[ref] = counts.get(ref, 0) + 1
    return counts


__all__ = [
    "DEFAULT_REVIEW_MONTHS",
    "DEPRECATION_GRACE_DAYS",
    "ReviewItem",
    "ReviewReport",
    "review",
    "scaffold",
    "usage_from_log",
]
