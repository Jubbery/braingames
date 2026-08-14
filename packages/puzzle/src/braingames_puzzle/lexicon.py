"""The scored word list and the index the solver searches.

Two jobs. **Ingest** turns raw word sources into entries scored 0-100 for fill
quality, because a legal grid full of words nobody knows is a bad puzzle and the
solver needs a gradient to prefer good fill. **Indexing** turns those entries
into per-length bitsets so constraint propagation is a bitwise AND rather than a
scan.

Known gap: the sources here are single words. Real crossword lexicons carry
multi-word phrases (``SLIPPERYSLOPE``, ``ONTHEROCKS``) which are what make long
theme entries and lively fill possible. The ingest path accepts them — entries
keep a ``display`` form with spaces — but the bundled sources do not supply any.
Swapping in a constructor wordlist is a data change, not a code change.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

ALPHA = re.compile(r"^[A-Z]+$")
MIN_LENGTH = 3
MAX_LENGTH = 21

#: Score bands. The numbers matter less than the ordering: the solver prefers
#: high-scoring fill, and the quality gates in docs/04 §4.5 are expressed
#: against this scale.
SCORE_TOP = 95  # among the most frequent English words
SCORE_COMMON = 80  # frequent
SCORE_FAMILIAR = 65  # in a "popular words" list
SCORE_VALID = 40  # in the dictionary, and nothing more
#: Nothing in the bundled sources scores here — a dictionary word is the worst
#: attestation they can express. It is the band a constructor wordlist uses for
#: entries a human has judged as junk, and the absolute floor exists for it.
SCORE_MARGINAL = 20


@dataclass(frozen=True, slots=True)
class LexiconEntry:
    word: str  # A-Z, no spaces — what goes in the grid
    display: str  # "SLIPPERY SLOPE" — what a human reads
    score: int  # 0-100, higher is better fill
    obscurity: float  # 0-1, drives the crossing-fairness rule
    source: str
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def length(self) -> int:
        return len(self.word)


def normalize(raw: str) -> tuple[str, str] | None:
    """(grid form, display form), or None if unusable.

    Spaces, hyphens and punctuation are stripped for the grid but kept for
    display — that distinction is what lets a multi-word phrase be an entry.
    """
    display = " ".join(raw.strip().split())
    if not display:
        return None
    word = re.sub(r"[^A-Za-z]", "", display).upper()
    if not ALPHA.match(word) or not MIN_LENGTH <= len(word) <= MAX_LENGTH:
        return None
    return word, display.upper()


# ----------------------------------------------------------------------
# Ingest
# ----------------------------------------------------------------------


@dataclass
class Source:
    """One raw word source and how much authority it carries."""

    name: str
    path: Path
    #: Ranked sources score by position; unranked ones get a flat score.
    ranked: bool = False
    flat_score: int = SCORE_VALID


def _read_words(path: Path) -> Iterator[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                yield line


def _rank_score(rank: int, total: int) -> int:
    """Frequency rank to score. Steep at the top, flat in the tail."""
    if total <= 1:
        return SCORE_TOP
    fraction = rank / total
    if fraction < 0.10:
        return SCORE_TOP
    if fraction < 0.50:
        return SCORE_COMMON + int((SCORE_TOP - SCORE_COMMON) * (0.50 - fraction) / 0.40)
    return SCORE_FAMILIAR + int((SCORE_COMMON - SCORE_FAMILIAR) * (1.0 - fraction) / 0.50)


#: Letters that mark a word as unusual, weighted by how much. Crude, and the
#: best signal available for the dictionary tail — see the module docstring.
RARE_LETTERS = {"J": 1.0, "Q": 1.0, "X": 1.0, "Z": 1.0, "K": 0.5, "V": 0.5, "W": 0.4, "Y": 0.3}


def rarity(word: str) -> float:
    """0-0.35, from unusual letters. A weak proxy, used only in the tail."""
    if not word:
        return 0.0
    return min(0.35, sum(RARE_LETTERS.get(c, 0.0) for c in word) / len(word))


def length_oddity(word: str) -> float:
    """0-0.15. A long word is harder on a solver than a short one of equal fame."""
    return min(0.15, max(0, len(word) - 8) * 0.02)


def ingest(sources: Iterable[Source]) -> list[LexiconEntry]:
    """Merge sources into one scored lexicon. Highest score wins on collision."""
    best: dict[str, LexiconEntry] = {}

    for source in sources:
        words = list(_read_words(source.path))
        total = len(words)

        for rank, raw in enumerate(words):
            pair = normalize(raw)
            if pair is None:
                continue
            word, display = pair

            score = _rank_score(rank, total) if source.ranked else source.flat_score
            score = max(0, min(100, score))
            existing = best.get(word)
            if existing is not None and existing.score >= score:
                continue

            best[word] = LexiconEntry(
                word=word,
                display=display,
                score=score,
                obscurity=obscurity_for(score, word),
                source=source.name,
            )

    return sorted(best.values(), key=lambda e: (-e.score, e.word))


def obscurity_for(score: int, word: str = "") -> float:
    """Map attestation and intrinsic oddity to 0-1 obscurity.

    ``score`` and ``obscurity`` answer different questions and the split is
    deliberate. Score is *attestation*: how well evidenced the word is in the
    sources, nothing more. Obscurity is *solver fairness*: how likely someone is
    to be stuck staring at it. Length and unusual letters belong to the second
    only — a nine-letter dictionary word is no less attested than a four-letter
    one, it is just harder to guess — and folding them into the score instead
    was a real bug: it moved every long word below the solver's floor without
    changing candidate ordering at all, because ordering is per-length already.

    Non-linear on purpose: the gap between 30 and 40 matters far more for
    fairness than the gap between 80 and 90, because that is where
    "recognisable" ends.

    **This signal is weak for the dictionary tail and knowing that matters.**
    The bundled sources give a frequency rank for only 10k words and a
    popularity flag for 25k more; the remaining ~335k get one flat score, so
    within that tail obscurity is inferred from length and unusual letters
    rather than known. It catches ZYZZYVA and misses ABERUNCATE. A constructor
    wordlist with human-assigned scores is the fix, and is the single highest-
    value data upgrade available to this pipeline.
    """
    if score >= SCORE_COMMON:
        base = max(0.0, (100 - score) / 400)
    elif score >= SCORE_FAMILIAR:
        base = 0.05 + (SCORE_COMMON - score) / 100
    elif score >= SCORE_VALID:
        base = 0.25 + (SCORE_FAMILIAR - score) / 100
    else:
        base = 0.52
    return round(min(1.0, base + length_oddity(word) + rarity(word)), 3)


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------

HEADER = "word\tdisplay\tscore\tobscurity\tsource\ttags"


def save(entries: Iterable[LexiconEntry], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8") as fh:
        fh.write(HEADER + "\n")
        for e in entries:
            fh.write(
                f"{e.word}\t{e.display}\t{e.score}\t{e.obscurity}\t{e.source}\t"
                f"{','.join(sorted(e.tags))}\n"
            )
            count += 1
    return count


def load(path: Path) -> list[LexiconEntry]:
    if not path.exists():
        raise FileNotFoundError(f"No lexicon at {path}. Build one with `bg puzzle lexicon ingest`.")
    out: list[LexiconEntry] = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n")
        if header != HEADER:
            raise ValueError(f"Unexpected lexicon header in {path}: {header!r}")
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 6:
                continue
            word, display, score, obscurity, source, tags = parts
            out.append(
                LexiconEntry(
                    word=word,
                    display=display,
                    score=int(score),
                    obscurity=float(obscurity),
                    source=source,
                    tags=frozenset(t for t in tags.split(",") if t),
                )
            )
    return out


# ----------------------------------------------------------------------
# The solver's index
# ----------------------------------------------------------------------


def bits_from_indices(indices: Iterable[int], nbits: int) -> int:
    """Pack word indices into a bitset.

    Built through a bytearray rather than repeated ``mask |= 1 << i``: the
    latter reallocates a growing big integer on every set bit and is quadratic.
    """
    buf = bytearray((nbits + 7) // 8)
    for i in indices:
        buf[i >> 3] |= 1 << (i & 7)
    return int.from_bytes(buf, "little")


def iter_bits(mask: int) -> Iterator[int]:
    """Set-bit indices, lowest first."""
    while mask:
        low = mask & -mask
        yield low.bit_length() - 1
        mask ^= low


class LexiconIndex:
    """Per-length word tables with letter-position bitsets.

    Words within a length are sorted by score descending, so walking a candidate
    mask from its lowest set bit yields the best fill first — the ordering the
    solver wants comes free from the bit order.
    """

    def __init__(self, entries: Iterable[LexiconEntry]) -> None:
        by_length: dict[int, list[LexiconEntry]] = {}
        for entry in entries:
            by_length.setdefault(entry.length, []).append(entry)

        self.words: dict[int, list[str]] = {}
        self.entries: dict[int, list[LexiconEntry]] = {}
        self.scores: dict[int, list[int]] = {}
        self.full: dict[int, int] = {}
        #: masks[length][position][letter_ord] -> bitset of matching word indices
        self.masks: dict[int, list[list[int]]] = {}
        self.index_of: dict[str, tuple[int, int]] = {}

        for length, group in by_length.items():
            group.sort(key=lambda e: (-e.score, e.word))
            self.entries[length] = group
            self.words[length] = [e.word for e in group]
            self.scores[length] = [e.score for e in group]
            n = len(group)
            self.full[length] = (1 << n) - 1

            buckets: list[list[list[int]]] = [[[] for _ in range(26)] for _ in range(length)]
            for i, entry in enumerate(group):
                self.index_of[entry.word] = (length, i)
                for pos, ch in enumerate(entry.word):
                    buckets[pos][ord(ch) - 65].append(i)

            self.masks[length] = [
                [bits_from_indices(bucket, n) for bucket in position] for position in buckets
            ]

    @property
    def size(self) -> int:
        return sum(len(v) for v in self.words.values())

    @property
    def lengths(self) -> list[int]:
        return sorted(self.words)

    def count(self, length: int) -> int:
        return len(self.words.get(length, ()))

    def mask_for(self, length: int, position: int, letter: str) -> int:
        return self.masks[length][position][ord(letter) - 65]

    def entry(self, length: int, index: int) -> LexiconEntry:
        return self.entries[length][index]

    def word(self, length: int, index: int) -> str:
        return self.words[length][index]

    def has_length(self, length: int) -> bool:
        return length in self.words and bool(self.words[length])


__all__ = [
    "MAX_LENGTH",
    "MIN_LENGTH",
    "SCORE_COMMON",
    "SCORE_FAMILIAR",
    "SCORE_MARGINAL",
    "SCORE_TOP",
    "SCORE_VALID",
    "LexiconEntry",
    "LexiconIndex",
    "Source",
    "bits_from_indices",
    "ingest",
    "iter_bits",
    "length_oddity",
    "load",
    "normalize",
    "obscurity_for",
    "rarity",
    "save",
]
