"""Load and validate the knowledge base.

Validation fails loudly and names the file and field. A malformed document that
loads silently is worse than one that crashes the worker: it degrades every
prompt it touches with no signal.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from . import frontmatter
from .models import DIR_KINDS, Defect, Doc, Frontmatter, Kind, Stage, Status


class KnowledgeBaseError(RuntimeError):
    """Raised when the knowledge base cannot be loaded at all."""


class KnowledgeBase:
    """An immutable, validated view of ``knowledge/``."""

    def __init__(self, docs: Iterable[Doc], defects: Iterable[Defect], root: Path) -> None:
        self._docs: dict[str, Doc] = {d.id: d for d in docs}
        self.defects: list[Defect] = list(defects)
        self.root = root

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, root: Path, *, strict: bool = True) -> KnowledgeBase:
        """Read every document under ``root``.

        With ``strict`` (the default), any error-severity defect raises. The
        nightly worker always loads strictly; ``bg kb validate`` loads
        non-strictly so it can report every problem at once instead of the
        first one.
        """
        if not root.is_dir():
            raise KnowledgeBaseError(f"Knowledge directory not found: {root}")

        docs: list[Doc] = []
        defects: list[Defect] = []

        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in frontmatter.SUPPORTED:
                continue
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue

            rel = path.relative_to(root).as_posix()
            try:
                parsed = frontmatter.read(path)
            except frontmatter.FrontmatterError as exc:
                defects.append(Defect(severity="error", doc=rel, field=None, message=str(exc)))
                continue

            try:
                meta = Frontmatter.model_validate(parsed.meta)
            except ValidationError as exc:
                for err in exc.errors():
                    field = ".".join(str(p) for p in err["loc"]) or None
                    defects.append(
                        Defect(severity="error", doc=rel, field=field, message=err["msg"])
                    )
                continue

            doc = Doc(meta=meta, body=parsed.body, path=path, lang=parsed.lang)
            defects.extend(_check_placement(doc, root))
            docs.append(doc)

        defects.extend(_check_collection(docs))

        kb = cls(docs, defects, root)
        if strict and kb.errors:
            joined = "\n  ".join(str(d) for d in kb.errors)
            raise KnowledgeBaseError(
                f"Knowledge base failed validation with {len(kb.errors)} error(s):\n  {joined}"
            )
        return kb

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    @property
    def errors(self) -> list[Defect]:
        return [d for d in self.defects if d.severity == "error"]

    @property
    def warnings(self) -> list[Defect]:
        return [d for d in self.defects if d.severity == "warning"]

    def all(self) -> list[Doc]:
        return sorted(self._docs.values(), key=lambda d: d.ref)

    def get(self, ident: str) -> Doc | None:
        """Look up by bare id (``scene-particle-field``) or ref (``patterns/...``)."""
        if ident in self._docs:
            return self._docs[ident]
        tail = ident.split("/")[-1].split("@")[0]
        return self._docs.get(tail)

    def active(self, stage: Stage | None = None) -> list[Doc]:
        docs = [d for d in self.all() if d.meta.status is Status.ACTIVE]
        if stage is not None:
            docs = [d for d in docs if stage in d.meta.applies_to]
        return docs

    def core(self, stage: Stage) -> list[Doc]:
        """Spine documents for a stage, in assembly order."""
        from .router import sort_key

        return sorted((d for d in self.active(stage) if d.meta.kind is Kind.CORE), key=sort_key)

    def routable(self, stage: Stage) -> list[Doc]:
        """Everything the router may select — active, on-stage, non-core."""
        return [d for d in self.active(stage) if d.meta.kind is not Kind.CORE]

    # ------------------------------------------------------------------

    def version(self) -> str:
        """Content hash of the active document set.

        Recorded on every generated puzzle so a quality regression can be
        bisected against the knowledge base the same way you would bisect code.
        """
        h = hashlib.sha256()
        for doc in self.active():
            h.update(doc.ref.encode())
            h.update(str(doc.meta.version).encode())
            h.update(doc.body_hash.encode())
        return f"kb-sha256-{h.hexdigest()[:16]}"

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for doc in self.all():
            counts[doc.meta.kind.value] = counts.get(doc.meta.kind.value, 0) + 1
        counts["total"] = len(self._docs)
        counts["active"] = len(self.active())
        counts["measured_tokens"] = sum(
            d.meta.tokens or 0 for d in self.active() if not d.tokens_are_stale
        )
        return counts


# ----------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------


def _check_placement(doc: Doc, root: Path) -> list[Defect]:
    rel = doc.path.relative_to(root)
    out: list[Defect] = []

    if doc.path.stem != doc.meta.id:
        out.append(
            Defect(
                severity="error",
                doc=rel.as_posix(),
                field="id",
                message=(
                    f"id {doc.meta.id!r} must match the filename stem {doc.path.stem!r}. "
                    f"Rename one so references stay predictable."
                ),
            )
        )

    top = rel.parts[0] if len(rel.parts) > 1 else ""
    expected = DIR_KINDS.get(top)
    if expected is None:
        out.append(
            Defect(
                severity="error",
                doc=rel.as_posix(),
                field=None,
                message=(
                    f"Directory {top!r} is not a knowledge kind. "
                    f"Expected one of {sorted(DIR_KINDS)}."
                ),
            )
        )
    elif expected is not doc.meta.kind:
        out.append(
            Defect(
                severity="error",
                doc=rel.as_posix(),
                field="kind",
                message=f"kind is {doc.meta.kind.value!r} but the file lives under {top!r}/",
            )
        )

    # Core is always loaded for its stages, so it needs no trigger. Anything
    # else without one can never be selected.
    if doc.meta.kind is not Kind.CORE and not doc.meta.triggers.is_reachable:
        out.append(
            Defect(
                severity="error",
                doc=rel.as_posix(),
                field="triggers",
                message=(
                    "Document is unreachable: no triggers set. Add tags, tiers, "
                    "motif_keywords, or always:true — or it will never be routed in."
                ),
            )
        )

    if doc.tokens_are_implausible:
        out.append(
            Defect(
                severity="warning",
                doc=rel.as_posix(),
                field="tokens",
                message=(
                    f"Declared {doc.meta.tokens} tokens for a {len(doc.body)}-character body, "
                    f"which is below any plausible ratio. Budgeting will ignore it and use a "
                    f"pessimistic bound. Re-run `bg kb tokens`."
                ),
            )
        )

    if doc.meta.status is Status.ACTIVE:
        if doc.tokens_are_stale:
            reason = "never measured" if doc.meta.tokens is None else "stale (body changed)"
            out.append(
                Defect(
                    severity="warning",
                    doc=rel.as_posix(),
                    field="tokens",
                    message=f"Token count is {reason}. Run `bg kb tokens` — budgets need real numbers.",
                )
            )
        if doc.meta.kind in (Kind.PATTERN, Kind.TEMPLATE) and not doc.meta.evidence:
            out.append(
                Defect(
                    severity="warning",
                    doc=rel.as_posix(),
                    field="evidence",
                    message=(
                        "Active pattern/template with no evidence. What eval showed this "
                        "earns its tokens? See docs/11 §11.7."
                    ),
                )
            )
        if doc.meta.review_by and doc.meta.review_by < datetime.now(UTC).date():
            out.append(
                Defect(
                    severity="warning",
                    doc=rel.as_posix(),
                    field="review_by",
                    message=(
                        f"Past review date ({doc.meta.review_by.isoformat()}). Re-evaluate "
                        f"against the golden set or deprecate it."
                    ),
                )
            )
    return out


def _check_collection(docs: list[Doc]) -> list[Defect]:
    out: list[Defect] = []
    seen: dict[str, Doc] = {}
    for doc in docs:
        if doc.id in seen:
            out.append(
                Defect(
                    severity="error",
                    doc=doc.path.name,
                    field="id",
                    message=(
                        f"Duplicate id {doc.id!r}, also defined by {seen[doc.id].ref}. "
                        f"Bare ids must be unique so `read_knowledge` is unambiguous."
                    ),
                )
            )
        else:
            seen[doc.id] = doc

    known = set(seen)
    for doc in docs:
        for sup in doc.meta.supersedes:
            tail = sup.split("/")[-1].split("@")[0]
            if tail not in known:
                out.append(
                    Defect(
                        severity="warning",
                        doc=doc.ref,
                        field="supersedes",
                        message=f"References unknown document {sup!r}.",
                    )
                )
    return out


def today() -> date:
    return datetime.now(UTC).date()


__all__ = ["KnowledgeBase", "KnowledgeBaseError"]
