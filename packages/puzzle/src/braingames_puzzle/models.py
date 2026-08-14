"""Grid geometry and puzzle types.

The invariants in ``knowledge/core/construction-rules.md`` are enforced here in
code rather than trusted to the generator. A grid that violates one of them is
not a stylistic problem — it is unsolvable, and a solver will not tell you why.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property

BLACK = "#"
WHITE = "."


class Direction(StrEnum):
    ACROSS = "across"
    DOWN = "down"

    @property
    def letter(self) -> str:
        return "A" if self is Direction.ACROSS else "D"


@dataclass(frozen=True, slots=True)
class Slot:
    """One entry position in the grid: where it starts, which way, how long."""

    number: int
    direction: Direction
    row: int
    col: int
    length: int
    #: Flat cell indices, in reading order along the entry.
    cells: tuple[int, ...]

    @property
    def id(self) -> str:
        return f"{self.number}{self.direction.letter}"

    def position_of(self, cell: int) -> int:
        return self.cells.index(cell)


class GridPattern:
    """A black-square pattern. Immutable; slots and numbering are derived."""

    __slots__ = ("__dict__", "blacks", "size")

    def __init__(self, size: int, blacks: tuple[bool, ...]) -> None:
        if len(blacks) != size * size:
            raise ValueError(
                f"Expected {size * size} cells for a {size}x{size} grid, got {len(blacks)}"
            )
        self.size = size
        self.blacks = blacks

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> GridPattern:
        """Parse a pattern of ``.`` and ``#``, with or without newlines."""
        flat = "".join(text.split())
        if not flat:
            raise ValueError("Empty grid pattern")
        bad = set(flat) - {BLACK, WHITE}
        if bad:
            raise ValueError(f"Unexpected characters in pattern: {sorted(bad)}")

        size = int(len(flat) ** 0.5)
        if size * size != len(flat):
            raise ValueError(f"Pattern of {len(flat)} cells is not square")
        return cls(size, tuple(ch == BLACK for ch in flat))

    @classmethod
    def blank(cls, size: int) -> GridPattern:
        return cls(size, (False,) * (size * size))

    def with_black(self, indices: set[int]) -> GridPattern:
        blacks = list(self.blacks)
        for i in indices:
            blacks[i] = True
        return GridPattern(self.size, tuple(blacks))

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def index(self, row: int, col: int) -> int:
        return row * self.size + col

    def coords(self, index: int) -> tuple[int, int]:
        return divmod(index, self.size)

    def mirror(self, index: int) -> int:
        """The 180°-rotational partner of a cell."""
        return self.size * self.size - 1 - index

    def is_black(self, index: int) -> bool:
        return self.blacks[index]

    @property
    def black_count(self) -> int:
        return sum(self.blacks)

    def to_text(self, *, newlines: bool = True) -> str:
        rows = [
            "".join(BLACK if self.blacks[r * self.size + c] else WHITE for c in range(self.size))
            for r in range(self.size)
        ]
        return "\n".join(rows) if newlines else "".join(rows)

    # ------------------------------------------------------------------
    # Slots and numbering
    # ------------------------------------------------------------------

    @cached_property
    def slots(self) -> tuple[Slot, ...]:
        """Every entry in the grid, numbered by the standard convention.

        A cell is numbered when it starts an Across entry, a Down entry, or
        both. Numbers run left-to-right, top-to-bottom, and a cell that starts
        both directions shares one number.
        """
        size = self.size
        found: list[Slot] = []
        number = 0

        for row in range(size):
            for col in range(size):
                idx = self.index(row, col)
                if self.blacks[idx]:
                    continue

                starts_across = (col == 0 or self.blacks[idx - 1]) and (
                    col + 1 < size and not self.blacks[idx + 1]
                )
                starts_down = (row == 0 or self.blacks[idx - size]) and (
                    row + 1 < size and not self.blacks[idx + size]
                )
                if not (starts_across or starts_down):
                    continue

                number += 1
                if starts_across:
                    cells = []
                    c = col
                    while c < size and not self.blacks[self.index(row, c)]:
                        cells.append(self.index(row, c))
                        c += 1
                    found.append(Slot(number, Direction.ACROSS, row, col, len(cells), tuple(cells)))
                if starts_down:
                    cells = []
                    r = row
                    while r < size and not self.blacks[self.index(r, col)]:
                        cells.append(self.index(r, col))
                        r += 1
                    found.append(Slot(number, Direction.DOWN, row, col, len(cells), tuple(cells)))

        return tuple(found)

    @cached_property
    def word_count(self) -> int:
        return len(self.slots)

    @cached_property
    def slots_by_cell(self) -> dict[int, tuple[int, ...]]:
        """Cell index -> indices into ``slots`` of every entry covering it.

        In a legal grid every white cell appears in exactly two entries. This
        map is what the solver uses to propagate a letter to its crossing.
        """
        out: dict[int, list[int]] = {}
        for si, slot in enumerate(self.slots):
            for cell in slot.cells:
                out.setdefault(cell, []).append(si)
        return {cell: tuple(v) for cell, v in out.items()}

    @cached_property
    def white_cells(self) -> tuple[int, ...]:
        return tuple(i for i, b in enumerate(self.blacks) if not b)

    def runs(self) -> list[list[int]]:
        """Every maximal run of white cells, in both directions.

        Includes runs of length 1 and 2, which slots deliberately exclude —
        the point of this is to *find* them.
        """
        size = self.size
        out: list[list[int]] = []
        for r in range(size):
            run: list[int] = []
            for c in range(size):
                idx = self.index(r, c)
                if self.blacks[idx]:
                    if run:
                        out.append(run)
                    run = []
                else:
                    run.append(idx)
            if run:
                out.append(run)
        for c in range(size):
            run = []
            for r in range(size):
                idx = self.index(r, c)
                if self.blacks[idx]:
                    if run:
                        out.append(run)
                    run = []
                else:
                    run.append(idx)
            if run:
                out.append(run)
        return out

    def is_connected(self) -> bool:
        """Whole white area reachable from any white cell."""
        whites = self.white_cells
        if not whites:
            return False

        size = self.size
        seen = {whites[0]}
        queue = deque([whites[0]])
        while queue:
            idx = queue.popleft()
            row, col = self.coords(idx)
            for nr, nc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if not (0 <= nr < size and 0 <= nc < size):
                    continue
                n = self.index(nr, nc)
                if n not in seen and not self.blacks[n]:
                    seen.add(n)
                    queue.append(n)
        return len(seen) == len(whites)

    def is_symmetric(self) -> bool:
        return all(self.blacks[i] == self.blacks[self.mirror(i)] for i in range(len(self.blacks)))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, GridPattern)
            and self.size == other.size
            and self.blacks == other.blacks
        )

    def __hash__(self) -> int:
        return hash((self.size, self.blacks))

    def __repr__(self) -> str:
        return f"GridPattern(size={self.size}, black={self.black_count}, words={self.word_count})"


@dataclass(frozen=True, slots=True)
class ThemePlacement:
    """A theme entry pinned to a slot before filling begins."""

    slot_id: str
    answer: str


@dataclass
class Fill:
    """A completed (or partial) grid fill."""

    pattern: GridPattern
    #: slot id -> answer
    answers: dict[str, str]

    @property
    def is_complete(self) -> bool:
        return len(self.answers) == self.pattern.word_count

    def letters(self) -> list[str | None]:
        """Flat per-cell letters; None where unfilled, ``#`` for black."""
        out: list[str | None] = [BLACK if b else None for b in self.pattern.blacks]
        by_id = {s.id: s for s in self.pattern.slots}
        for slot_id, answer in self.answers.items():
            slot = by_id[slot_id]
            for pos, cell in enumerate(slot.cells):
                out[cell] = answer[pos]
        return out

    def grid_text(self) -> str:
        letters = self.letters()
        size = self.pattern.size
        return "\n".join(
            "".join(letters[r * size + c] or WHITE for c in range(size)) for r in range(size)
        )


@dataclass
class Puzzle:
    """A puzzle at any stage of completion: grid, answers, and whatever else exists yet.

    Clues arrive a stage after the fill, so ``clues`` is allowed to be empty and
    the QA pass reports which of its checks that made inapplicable rather than
    quietly passing them.
    """

    pattern: GridPattern
    #: slot id -> answer, in grid form (A-Z, no spaces)
    answers: dict[str, str]
    #: slot id -> clue text
    clues: dict[str, str] = field(default_factory=dict)
    #: slot ids carrying the theme
    theme_slots: tuple[str, ...] = ()
    theme_title: str | None = None
    tier: int = 2
    #: cell index -> the multi-letter string that cell holds. Empty in a normal
    #: puzzle; a rebus is the exception that every consumer has to handle.
    rebus: dict[int, str] = field(default_factory=dict)

    @property
    def fill(self) -> Fill:
        return Fill(pattern=self.pattern, answers=self.answers)

    @property
    def has_clues(self) -> bool:
        return bool(self.clues)

    def slot(self, slot_id: str) -> Slot:
        for s in self.pattern.slots:
            if s.id == slot_id:
                return s
        raise KeyError(f"No slot {slot_id!r} in this grid")


__all__ = [
    "BLACK",
    "WHITE",
    "Direction",
    "Fill",
    "GridPattern",
    "Puzzle",
    "Slot",
    "ThemePlacement",
]
