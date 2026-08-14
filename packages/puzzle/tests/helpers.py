"""Small hand-built grids for tests.

A 5x5 is large enough to exercise numbering, crossings and symmetry and small
enough that a broken variant can be read at a glance, which is what a
one-defect-per-check corpus needs.
"""

from __future__ import annotations

from braingames_puzzle.lexicon import LexiconEntry, LexiconIndex
from braingames_puzzle.models import GridPattern, Puzzle

#: A legal, symmetric, fully-checked 5x5 with no black squares at all.
OPEN_5 = """
.....
.....
.....
.....
.....
"""

#: 5x5 with a symmetric pair of black squares.
CORNERS_5 = """
..#..
.....
.....
.....
..#..
"""

#: A genuine double word square: ten distinct, familiar entries, every one a
#: real word in both directions. Produced by the solver against the real
#: lexicon and pinned here so the tests carry no data dependency. Hand-picked
#: letters do not work — they leave junk in the Down entries, which then trips
#: the abbreviation and agreement checks and makes a "clean" corpus dirty.
WORD_SQUARE_5 = ("ABOUT", "CRUSH", "RATIO", "EVENS", "SARGE")


def open_answers() -> dict[str, str]:
    """Every entry of the 5x5 word square, keyed by slot id."""
    pattern = GridPattern.parse(OPEN_5)
    return {
        slot.id: "".join(WORD_SQUARE_5[cell // 5][cell % 5] for cell in slot.cells)
        for slot in pattern.slots
    }


def clean_puzzle(**overrides: object) -> Puzzle:
    """A puzzle that passes every applicable check.

    Tier 0 by default: the word-count bands are 15x15 figures and a 5x5 has no
    business being judged against them.
    """
    answers = open_answers()
    fields: dict[str, object] = {
        "pattern": GridPattern.parse(OPEN_5),
        "answers": answers,
        "clues": {
            slot_id: f"Vague gesture number {i}" for i, slot_id in enumerate(sorted(answers), 1)
        },
        "theme_slots": (),
        "tier": 0,
    }
    fields.update(overrides)
    return Puzzle(**fields)  # type: ignore[arg-type]


def index_for(
    words: dict[str, tuple[int, float]],
    tags: dict[str, set[str]] | None = None,
) -> LexiconIndex:
    """A tiny lexicon: word -> (score, obscurity)."""
    tags = tags or {}
    return LexiconIndex(
        LexiconEntry(
            word=word,
            display=word,
            score=score,
            obscurity=obscurity,
            source="test",
            tags=frozenset(tags.get(word, ())),
        )
        for word, (score, obscurity) in words.items()
    )


def index_for_puzzle(puzzle: Puzzle, *, score: int = 80, obscurity: float = 0.1) -> LexiconIndex:
    """A lexicon that knows every answer in a puzzle and finds none of them odd."""
    return index_for({answer: (score, obscurity) for answer in puzzle.answers.values()})
