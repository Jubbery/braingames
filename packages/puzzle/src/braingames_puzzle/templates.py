"""Grid templates: validation, generation, and theme placement.

The design decision behind this module (docs/04 §4.1): grids are **selected**,
not generated per puzzle. Legality — 180° symmetry, full interlock, minimum
word length, connectivity — is established once at ingest, so a template that
passes is legal forever. Buying that property once is enormously cheaper than
re-establishing it on every generation run, and it removes the failure mode
where a model produces a plausible-looking but unsolvable grid.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from enum import IntEnum

from pydantic import BaseModel, Field

from .models import Direction, GridPattern, Slot

MIN_WORD_LENGTH = 3

#: Word-count band per difficulty tier (docs/01 §1.2). Tier 3 is wider open,
#: which is what makes it harder to fill and harder to solve.
WORD_COUNT_BANDS: dict[int, tuple[int, int]] = {
    1: (74, 78),
    2: (72, 78),
    3: (68, 72),
}

#: Below this length an Across slot is not a plausible theme entry.
MIN_THEME_LENGTH = 7


class Violation(IntEnum):
    ASYMMETRIC = 1
    UNCHECKED_CELL = 2
    SHORT_ENTRY = 3
    DISCONNECTED = 4
    WORD_COUNT = 5
    EMPTY = 6


@dataclass(frozen=True)
class Defect:
    kind: Violation
    detail: str

    def __str__(self) -> str:
        return f"{self.kind.name}: {self.detail}"


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def validate(pattern: GridPattern, *, band: tuple[int, int] | None = None) -> list[Defect]:
    """Every legality rule, checked. An empty list means the grid is usable.

    This runs once per template at ingest, never per puzzle.
    """
    defects: list[Defect] = []

    if not pattern.white_cells:
        return [Defect(Violation.EMPTY, "No white cells")]

    if not pattern.is_symmetric():
        asymmetric = [
            i
            for i in range(len(pattern.blacks))
            if pattern.blacks[i] != pattern.blacks[pattern.mirror(i)]
        ]
        defects.append(
            Defect(
                Violation.ASYMMETRIC,
                f"{len(asymmetric)} cells lack a 180° partner (first at {pattern.coords(asymmetric[0])})",
            )
        )

    short = [run for run in pattern.runs() if len(run) < MIN_WORD_LENGTH]
    if short:
        r, c = pattern.coords(short[0][0])
        defects.append(
            Defect(
                Violation.SHORT_ENTRY,
                f"{len(short)} run(s) shorter than {MIN_WORD_LENGTH} (first at row {r}, col {c})",
            )
        )

    # Every white cell must belong to both an Across and a Down entry. This is
    # the "no unchecked squares" rule, and it is what makes crossings solvable.
    by_cell = pattern.slots_by_cell
    unchecked = [cell for cell in pattern.white_cells if len(by_cell.get(cell, ())) != 2]
    if unchecked:
        r, c = pattern.coords(unchecked[0])
        defects.append(
            Defect(
                Violation.UNCHECKED_CELL,
                f"{len(unchecked)} cell(s) not crossed in both directions (first at row {r}, col {c})",
            )
        )

    if not pattern.is_connected():
        defects.append(Defect(Violation.DISCONNECTED, "White space is not one connected region"))

    if band is not None:
        low, high = band
        if not low <= pattern.word_count <= high:
            defects.append(
                Defect(
                    Violation.WORD_COUNT,
                    f"{pattern.word_count} words, outside the {low}-{high} band",
                )
            )

    return defects


def is_legal(pattern: GridPattern) -> bool:
    return not validate(pattern)


# ----------------------------------------------------------------------
# Symmetry of entries
# ----------------------------------------------------------------------


def symmetric_partner(pattern: GridPattern, slot: Slot) -> Slot | None:
    """The slot occupying this one's 180°-rotated position.

    Returns the slot itself when it is centred and maps onto itself. Theme
    entries must be placeable in partner pairs, so this is what makes a theme
    set placeable or not.
    """
    mirrored = tuple(sorted(pattern.mirror(c) for c in slot.cells))
    for other in pattern.slots:
        if other.direction is slot.direction and tuple(sorted(other.cells)) == mirrored:
            return other
    return None


def theme_slots(pattern: GridPattern, *, min_length: int = MIN_THEME_LENGTH) -> list[Slot]:
    """Across slots long enough to carry a theme entry, longest first."""
    return sorted(
        (s for s in pattern.slots if s.direction is Direction.ACROSS and s.length >= min_length),
        key=lambda s: (-s.length, s.number),
    )


def theme_capacity(
    pattern: GridPattern, *, min_length: int = MIN_THEME_LENGTH
) -> dict[int, tuple[int, int]]:
    """Entry length -> (symmetric pairs available, centred slots available).

    This is what turns theme ideation from guesswork into a constrained ask.
    A grid does not accept arbitrary theme entries: they pair up by equal length
    under 180° rotation, so a template offering four 15-slot pairs and nothing
    at 12 cannot carry a pair of twelve-letter entries however good they are.
    Handing the ideation stage the available shapes up front — "two entries of
    15, or three of 9" — is far cheaper than generating a theme and discovering
    at placement time that it does not fit.
    """
    pairs: dict[int, int] = {}
    singles: dict[int, int] = {}
    seen: set[str] = set()

    for slot in theme_slots(pattern, min_length=min_length):
        if slot.id in seen:
            continue
        partner = symmetric_partner(pattern, slot)
        seen.add(slot.id)
        if partner is None or partner.length < min_length:
            continue
        if partner.id == slot.id:
            singles[slot.length] = singles.get(slot.length, 0) + 1
        else:
            pairs[slot.length] = pairs.get(slot.length, 0) + 1
            seen.add(partner.id)

    return {
        length: (pairs.get(length, 0), singles.get(length, 0))
        for length in sorted(set(pairs) | set(singles))
    }


def place_theme(pattern: GridPattern, lengths: list[int]) -> dict[int, str] | None:
    """Assign theme entry lengths to symmetrically-placed Across slots.

    Returns a map from the *index into ``lengths``* to a slot id, or None when
    the set cannot be placed. Working in index space rather than by length lets
    the caller keep two entries of the same length distinct.

    The rule from ``knowledge/core/construction-rules.md``: entries pair up by
    equal length under 180° rotation, with at most one odd entry out, which must
    sit in a self-symmetric (centred) slot.
    """
    if not lengths:
        return {}

    candidates = theme_slots(pattern, min_length=min(lengths))
    partners: dict[str, Slot | None] = {s.id: symmetric_partner(pattern, s) for s in candidates}
    by_id = {s.id: s for s in candidates}

    # Group available slots into partner pairs and self-symmetric singles.
    pairs: list[tuple[Slot, Slot]] = []
    singles: list[Slot] = []
    seen: set[str] = set()
    for slot in candidates:
        if slot.id in seen:
            continue
        partner = partners[slot.id]
        if partner is None or partner.id not in by_id:
            seen.add(slot.id)
            continue
        if partner.id == slot.id:
            singles.append(slot)
            seen.add(slot.id)
        else:
            pairs.append((slot, partner))
            seen.update({slot.id, partner.id})

    # Longest first: long entries are the constrained ones, and placing them
    # first avoids burning a long slot on a short entry.
    order = sorted(range(len(lengths)), key=lambda i: -lengths[i])
    remaining = list(order)
    placement: dict[int, str] = {}
    used_pairs: set[int] = set()
    used_singles: set[str] = set()

    while remaining:
        i = remaining[0]
        length = lengths[i]
        twin = next((j for j in remaining[1:] if lengths[j] == length), None)

        if twin is not None:
            slot_pair = next(
                (
                    p
                    for p, (a, _b) in enumerate(pairs)
                    if p not in used_pairs and a.length == length
                ),
                None,
            )
            if slot_pair is None:
                return None
            a, b = pairs[slot_pair]
            placement[i] = a.id
            placement[twin] = b.id
            used_pairs.add(slot_pair)
            remaining.remove(i)
            remaining.remove(twin)
            continue

        # Odd one out — needs a self-symmetric slot.
        single = next((s for s in singles if s.id not in used_singles and s.length == length), None)
        if single is None:
            return None
        placement[i] = single.id
        used_singles.add(single.id)
        remaining.remove(i)

    return placement


# ----------------------------------------------------------------------
# Template records
# ----------------------------------------------------------------------


class SlotSpec(BaseModel):
    """A theme-capable slot, as stored on a template."""

    id: str
    number: int
    row: int
    col: int
    length: int


class GridTemplate(BaseModel):
    """A vetted, reusable black-square pattern."""

    id: str
    size: int
    pattern: str
    word_count: int
    black_count: int
    theme_slots: list[SlotSpec] = Field(default_factory=list)
    #: Entry length -> [symmetric pairs, centred slots]. What the ideation
    #: stage is handed so it proposes a theme this grid can actually hold.
    theme_capacity: dict[int, tuple[int, int]] = Field(default_factory=dict)
    max_slot_length: int
    #: Mean entry length. A proxy for fill difficulty — wider grids have fewer,
    #: longer entries and far fewer legal fills.
    openness: float
    difficulty_band: int

    #: Learned prior, updated as the solver uses this template. The cheapest
    #: available lever on end-to-end generation latency.
    fill_attempts: int = 0
    fill_successes: int = 0
    avg_fill_ms: int | None = None

    @property
    def success_rate(self) -> float:
        return self.fill_successes / self.fill_attempts if self.fill_attempts else 0.5

    def to_pattern(self) -> GridPattern:
        return GridPattern.parse(self.pattern)


def describe(pattern: GridPattern, *, band: int) -> GridTemplate:
    """Build a template record from a validated pattern."""
    slots = pattern.slots
    flat = pattern.to_text(newlines=False)
    digest = hashlib.sha256(flat.encode()).hexdigest()[:12]

    return GridTemplate(
        id=f"g{pattern.size}-{band}-{digest}",
        size=pattern.size,
        pattern=flat,
        word_count=pattern.word_count,
        black_count=pattern.black_count,
        theme_slots=[
            SlotSpec(id=s.id, number=s.number, row=s.row, col=s.col, length=s.length)
            for s in theme_slots(pattern)
        ],
        theme_capacity=theme_capacity(pattern),
        max_slot_length=max(s.length for s in slots),
        openness=round(sum(s.length for s in slots) / len(slots), 3),
        difficulty_band=band,
    )


# ----------------------------------------------------------------------
# Generation
# ----------------------------------------------------------------------


def _has_short_run(blacks: list[bool], size: int) -> bool:
    """Any maximal white run of length 1 or 2, in either direction.

    Adding a black square can only split runs into shorter ones, so checking
    after each placement and undoing on failure keeps the grid legal by
    construction rather than by rejection sampling.
    """
    for r in range(size):
        run = 0
        for c in range(size):
            if blacks[r * size + c]:
                if 0 < run < MIN_WORD_LENGTH:
                    return True
                run = 0
            else:
                run += 1
        if 0 < run < MIN_WORD_LENGTH:
            return True

    for c in range(size):
        run = 0
        for r in range(size):
            if blacks[r * size + c]:
                if 0 < run < MIN_WORD_LENGTH:
                    return True
                run = 0
            else:
                run += 1
        if 0 < run < MIN_WORD_LENGTH:
            return True

    return False


#: How strongly to avoid placing a black square next to an existing one.
#:
#: Measured, not guessed. Clustering blacks into blobs *lowers* the word count
#: for a given black count, because a blob's interior squares split no new runs;
#: spreading them maximises run-splitting. At 36-44 blacks this moves words per
#: black square from 1.73 to 1.84 and the median word count from 70 to 74, which
#: is the difference between hitting the band-1 range and missing it.
SPREAD_BIAS = 6.0


def _neighbours(index: int, size: int) -> list[int]:
    row, col = divmod(index, size)
    out = []
    for nr, nc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
        if 0 <= nr < size and 0 <= nc < size:
            out.append(nr * size + nc)
    return out


def generate_pattern(
    size: int,
    target_black: int,
    rng: random.Random,
    *,
    spread: float = SPREAD_BIAS,
    max_stalls: int = 300,
) -> GridPattern | None:
    """Grow a symmetric pattern by placing mirror pairs that keep it legal.

    Rejection sampling over whole random patterns has a dismal yield — one
    badly-placed square near an edge creates a two-letter entry. Placing
    incrementally and undoing anything that breaks the minimum-length invariant
    converges instead, at roughly 99% legality.

    Placement is weighted away from existing black squares (see ``SPREAD_BIAS``).
    """
    n = size * size
    blacks = [False] * n
    placed = 0
    stalls = 0

    while placed < target_black and stalls < max_stalls:
        pool = [i for i in range(n) if not blacks[i]]
        if not pool:
            break
        weights = [
            1.0 / (1.0 + spread * sum(1 for j in _neighbours(i, size) if blacks[j])) for i in pool
        ]
        idx = rng.choices(pool, weights=weights, k=1)[0]
        mirror = n - 1 - idx

        blacks[idx] = True
        blacks[mirror] = True
        if _has_short_run(blacks, size):
            blacks[idx] = False
            blacks[mirror] = False
            stalls += 1
        else:
            placed = sum(blacks)
            stalls = 0

    pattern = GridPattern(size, tuple(blacks))
    return pattern if is_legal(pattern) else None


def generate_library(
    *,
    size: int = 15,
    per_band: int = 200,
    seed: int = 0,
    max_attempts_per_template: int = 400,
) -> list[GridTemplate]:
    """Generate a deduplicated library across all three difficulty bands."""
    rng = random.Random(seed)
    seen: set[str] = set()
    out: list[GridTemplate] = []

    # Calibrated against the measured words-per-black ratio of this generator
    # (~1.84), not copied from published grids: real NYT grids reach 78 words
    # with ~38 blacks by arranging them in bars, which random symmetric
    # placement does not reproduce. Fewer blacks means longer, more open
    # entries and a lower word count, which is what makes band 3 both harder to
    # fill and harder to solve.
    black_targets = {1: (40, 48), 2: (38, 46), 3: (32, 40)}

    for band, (low, high) in black_targets.items():
        made = 0
        attempts = 0
        while made < per_band and attempts < per_band * max_attempts_per_template:
            attempts += 1
            pattern = generate_pattern(size, rng.randint(low, high), rng)
            if pattern is None:
                continue
            if validate(pattern, band=WORD_COUNT_BANDS[band]):
                continue

            flat = pattern.to_text(newlines=False)
            if flat in seen:
                continue
            seen.add(flat)
            out.append(describe(pattern, band=band))
            made += 1

    return out


__all__ = [
    "MIN_THEME_LENGTH",
    "MIN_WORD_LENGTH",
    "WORD_COUNT_BANDS",
    "Defect",
    "GridTemplate",
    "SlotSpec",
    "Violation",
    "describe",
    "generate_library",
    "generate_pattern",
    "is_legal",
    "place_theme",
    "symmetric_partner",
    "theme_slots",
    "validate",
]
