"""Knowledge document types.

Implements the frontmatter schema in docs/11-agent-knowledge-base.md §11.2.
Every field earns its place:

* ``applies_to`` / ``triggers`` drive routing. A document nobody can route to
  is dead weight, so both are required and validated.
* ``tokens`` is *measured*, never estimated, or budgets are fiction.
* ``evidence`` is the anti-cruft mechanism — a document that cannot point at
  the eval result that justified it gets challenged at review time.
* ``review_by`` forces expiry. A knowledge base that only grows becomes noise,
  and noise measurably degrades model output.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class Kind(StrEnum):
    """What sort of knowledge this is. Determines the directory it lives in."""

    CORE = "core"
    PATTERN = "pattern"
    TEMPLATE = "template"
    INPUT_HANDLING = "input_handling"
    DOMAIN = "domain"
    POSTMORTEM = "postmortem"


#: Directory each kind lives under, relative to the knowledge root.
KIND_DIRS: dict[Kind, str] = {
    Kind.CORE: "core",
    Kind.PATTERN: "patterns",
    Kind.TEMPLATE: "templates",
    Kind.INPUT_HANDLING: "input-handling",
    Kind.DOMAIN: "domains",
    Kind.POSTMORTEM: "postmortems",
}
DIR_KINDS: dict[str, Kind] = {v: k for k, v in KIND_DIRS.items()}


class Stage(StrEnum):
    """Pipeline stages a document can apply to.

    The first five are runtime generation stages; the last three are the
    build-time coding agents working in this repo. One substrate, routed by
    ``applies_to`` — see docs/11-agent-knowledge-base.md §11.8.
    """

    THEME_IDEATION = "theme_ideation"
    CLUE_WRITING = "clue_writing"
    EDITORIAL_QA = "editorial_qa"
    THEME_SCENE_GENERATION = "theme_scene_generation"
    PROFILE_CLASSIFICATION = "profile_classification"
    CODEGEN_BACKEND = "codegen_backend"
    CODEGEN_FRONTEND = "codegen_frontend"
    CODEGEN_REVIEW = "codegen_review"


class Status(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class Triggers(BaseModel):
    """When the router should pull this document in.

    A document with no trigger at all is unreachable and fails validation.
    """

    model_config = ConfigDict(extra="forbid")

    always: bool = False
    tags: list[str] = Field(default_factory=list)
    motif_keywords: list[str] = Field(default_factory=list)
    tiers: list[int] = Field(default_factory=list)

    @field_validator("tags", "motif_keywords")
    @classmethod
    def _normalize(cls, v: list[str]) -> list[str]:
        return sorted({s.strip().lower() for s in v if s.strip()})

    @field_validator("tiers")
    @classmethod
    def _valid_tiers(cls, v: list[int]) -> list[int]:
        bad = [t for t in v if t not in (1, 2, 3)]
        if bad:
            raise ValueError(f"tiers must be 1, 2, or 3; got {bad}")
        return sorted(set(v))

    @property
    def is_reachable(self) -> bool:
        return bool(self.always or self.tags or self.motif_keywords or self.tiers)


class Frontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Kind
    applies_to: list[Stage] = Field(min_length=1)

    #: The index line. Written for the model, not for a human browsing folders:
    #: it is what gets a document read or ignored. Lead with "Use when...".
    summary: str = Field(min_length=20, max_length=400)

    triggers: Triggers = Field(default_factory=Triggers)
    version: int = Field(default=1, ge=1)
    status: Status = Status.DRAFT

    #: Assembly order. Lower sorts earlier. Ties break on id, so ordering is
    #: total and deterministic — see router.sort_key.
    priority: int = Field(default=100, ge=0, le=1000)

    #: Never drop this document to fit a budget.
    #:
    #: Budgeting is a quality tradeoff for most documents — losing a pattern
    #: costs some polish. For a few it is a correctness or safety tradeoff:
    #: silently dropping the injection-defense playbook because three other
    #: documents sorted ahead of it is not an acceptable failure mode. Required
    #: documents are admitted before anything else, and a budget too small to
    #: hold them is an error rather than a silent omission.
    required: bool = False

    #: Measured with count_tokens against the reference model. None until
    #: `bg kb tokens` has run.
    tokens: int | None = Field(default=None, ge=0)
    #: Body hash at the time tokens were measured. Lets validation detect a
    #: stale count without re-hitting the API.
    token_hash: str | None = None

    supersedes: list[str] = Field(default_factory=list)
    evidence: str | None = None
    review_by: date | None = None
    owner: str = "unassigned"

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG.match(v):
            raise ValueError(
                f"id {v!r} must be a lowercase hyphenated slug (e.g. 'scene-particle-field')"
            )
        return v

    @field_validator("applies_to")
    @classmethod
    def _dedupe_stages(cls, v: list[Stage]) -> list[Stage]:
        return sorted(set(v), key=lambda s: s.value)


class Doc(BaseModel):
    """A loaded knowledge document."""

    model_config = ConfigDict(frozen=True)

    meta: Frontmatter
    body: str
    path: Path
    #: Language hint for rendering code templates in the prompt.
    lang: str = "markdown"

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def ref(self) -> str:
        """Qualified reference, e.g. ``core/construction-rules``."""
        return f"{KIND_DIRS[self.meta.kind]}/{self.meta.id}"

    @property
    def versioned_ref(self) -> str:
        """``core/construction-rules@7`` — what gets recorded in provenance."""
        return f"{self.ref}@{self.meta.version}"

    @property
    def body_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def tokens_are_stale(self) -> bool:
        return self.meta.token_hash is None or self.meta.token_hash != self.body_hash

    @property
    def pessimistic_tokens(self) -> int:
        """Upper bound from character count, for when no trustworthy measurement exists."""
        return len(self.body) // 3 + 32

    @property
    def implausible_floor(self) -> int:
        """Below this, a declared token count cannot be true.

        English tokenizes around 4 characters per token and code rather denser.
        Nothing reaches 8, so a declared count under ``chars // 8`` means the
        number was hand-edited, measured against the wrong model, or measured
        against different content.
        """
        return len(self.body) // 8

    @property
    def tokens_are_implausible(self) -> bool:
        return self.meta.tokens is not None and self.meta.tokens < self.implausible_floor

    def cost_tokens(self) -> int:
        """Token cost for budgeting.

        Prefers the measured value, but only when it is both fresh and
        plausible. Otherwise falls back to a deliberately *pessimistic* bound,
        so a wrong number can never silently overrun a budget — the document
        just gets dropped sooner, which is visible in the route result.
        """
        if (
            self.meta.tokens is not None
            and not self.tokens_are_stale
            and not self.tokens_are_implausible
        ):
            return self.meta.tokens
        return self.pessimistic_tokens

    def render(self) -> str:
        """Deterministic prompt representation.

        The wrapper gives the model provenance so it can attribute guidance,
        and gives us a greppable marker when debugging an assembled prompt.
        """
        fence = (
            f"```{self.lang}\n{self.body.strip()}\n```"
            if self.lang != "markdown"
            else self.body.strip()
        )
        return f'<document ref="{self.ref}" version="{self.meta.version}">\n{fence}\n</document>'

    def index_line(self) -> str:
        return f"- {self.ref} — {self.meta.summary.strip()}"


class Defect(BaseModel):
    """A validation finding. Errors block; warnings are reported."""

    severity: str  # "error" | "warning"
    doc: str
    field: str | None
    message: str

    def __str__(self) -> str:
        where = f"{self.doc}" + (f".{self.field}" if self.field else "")
        return f"[{self.severity}] {where}: {self.message}"


__all__ = [
    "DIR_KINDS",
    "KIND_DIRS",
    "Defect",
    "Doc",
    "Frontmatter",
    "Kind",
    "Stage",
    "Status",
    "Triggers",
]
