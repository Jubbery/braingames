"""Mechanical QA: the blocking checks from docs/04 §4.7.

Every check is a pure function of a ``QAContext`` returning a list of defects,
which is what makes each one independently testable against a puzzle broken in
exactly one way. Nothing here calls a model. The editorial pass that does is a
separate stage and is advisory; this one is not skippable and is not advisory.

**Checks that cannot run say so.** Clues arrive a stage after the fill, so a
clue check on an unclued puzzle is not a pass — it is an inapplicable check, and
``Report.skipped`` records it. ``Report.publishable`` requires an empty skip
list precisely so that a puzzle cannot reach a solver on the strength of checks
that never ran.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .lexicon import LexiconIndex
from .models import Direction, Puzzle, Slot
from .templates import MIN_WORD_LENGTH, WORD_COUNT_BANDS

MAX_CLUE_CHARS = 120
OBSCURE_THRESHOLD = 0.7


class Severity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


@dataclass(frozen=True, slots=True)
class QADefect:
    check: str
    severity: Severity
    location: str
    issue: str
    suggested_fix: str | None = None

    def __str__(self) -> str:
        return f"[{self.severity}] {self.location}: {self.issue}"


class Tagger(Protocol):
    """Part-of-speech tagging, as much of it as this check needs.

    A protocol rather than a hard dependency so the NLP library stays out of
    this package's install and out of every test that does not exercise it.
    """

    def head(self, text: str) -> tuple[str, str] | None:
        """The head word of a phrase and its coarse tag (``NOUN``, ``VERB``…),
        or None when the phrase has no identifiable head."""


@dataclass
class QAContext:
    """Everything a check may look at. Checks take this, not loose arguments,
    so adding a signal later does not change fifteen signatures."""

    puzzle: Puzzle
    index: LexiconIndex | None = None
    #: Supplied by the caller when one is configured. See
    #: ``check_clue_answer_agreement`` for why its absence is a skip.
    tagger: Tagger | None = None

    @property
    def slots(self) -> tuple[Slot, ...]:
        return self.puzzle.pattern.slots

    def answer(self, slot_id: str) -> str:
        return self.puzzle.answers.get(slot_id, "")


@dataclass
class Report:
    defects: list[QADefect] = field(default_factory=list)
    #: Checks that could not run, and why. Never empty for an unclued puzzle.
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def by_severity(self, severity: Severity) -> list[QADefect]:
        return [d for d in self.defects if d.severity is severity]

    @property
    def blockers(self) -> list[QADefect]:
        return self.by_severity(Severity.BLOCKER)

    @property
    def publishable(self) -> bool:
        return not self.defects and not self.skipped

    def __str__(self) -> str:
        if self.publishable:
            return "clean"
        lines = [str(d) for d in self.defects]
        lines += [f"[skipped] {name}: {why}" for name, why in self.skipped]
        return "\n".join(lines)


class Skip(Exception):  # noqa: N818 - control flow, not a failure
    """Raised by a check that cannot run against this puzzle.

    Not named ``SkipError`` because it is not one: it travels the same road as
    ``StopIteration``, reporting that a check had nothing to judge rather than
    that something went wrong.
    """


# ----------------------------------------------------------------------
# Grid checks
# ----------------------------------------------------------------------


def check_symmetry(ctx: QAContext) -> list[QADefect]:
    """180° rotational symmetry."""
    pattern = ctx.puzzle.pattern
    if pattern.is_symmetric():
        return []
    offenders = [
        i
        for i in range(len(pattern.blacks))
        if pattern.blacks[i] != pattern.blacks[pattern.mirror(i)]
    ]
    return [
        QADefect(
            "check_symmetry",
            Severity.BLOCKER,
            "grid",
            f"{len(offenders)} cells have no rotational partner, first at {pattern.coords(offenders[0])}",
            "Mirror the black squares about the centre.",
        )
    ]


def check_all_squares_checked(ctx: QAContext) -> list[QADefect]:
    """Every white square belongs to both an Across and a Down entry.

    An unchecked square can only be reached from one direction, so a solver who
    does not know that one entry has no way in at all.
    """
    pattern = ctx.puzzle.pattern
    bad: list[int] = []
    for cell in pattern.white_cells:
        directions = {pattern.slots[si].direction for si in pattern.slots_by_cell.get(cell, ())}
        if directions != {Direction.ACROSS, Direction.DOWN}:
            bad.append(cell)
    if not bad:
        return []
    return [
        QADefect(
            "check_all_squares_checked",
            Severity.BLOCKER,
            "grid",
            f"{len(bad)} unchecked square(s), first at {pattern.coords(bad[0])}",
            "Add or move a black square so the run in the missing direction is at least 3 long.",
        )
    ]


def check_min_word_length(ctx: QAContext) -> list[QADefect]:
    pattern = ctx.puzzle.pattern
    short = [run for run in pattern.runs() if 0 < len(run) < MIN_WORD_LENGTH]
    if not short:
        return []
    return [
        QADefect(
            "check_min_word_length",
            Severity.BLOCKER,
            "grid",
            f"{len(short)} entr(y/ies) shorter than {MIN_WORD_LENGTH}, first at "
            f"{pattern.coords(short[0][0])}",
            "Remove the black square that truncates the run.",
        )
    ]


def check_connectivity(ctx: QAContext) -> list[QADefect]:
    if ctx.puzzle.pattern.is_connected():
        return []
    return [
        QADefect(
            "check_connectivity",
            Severity.BLOCKER,
            "grid",
            "The white squares form more than one region",
            "Open a square joining the isolated region to the rest of the grid.",
        )
    ]


#: The tier bands in ``templates`` are 15x15 figures. A mini or a midi has its
#: own economics and will bring its own table when those games arrive; until
#: then the check declines to judge a grid it has no band for rather than
#: applying the daily crossword's numbers to a 5x5.
BANDED_SIZE = 15


def check_word_count(ctx: QAContext) -> list[QADefect]:
    puzzle = ctx.puzzle
    if puzzle.pattern.size != BANDED_SIZE:
        raise Skip(f"no word-count band defined for {puzzle.pattern.size}x{puzzle.pattern.size}")
    band = WORD_COUNT_BANDS.get(puzzle.tier)
    if band is None:
        raise Skip(f"no word-count band defined for tier {puzzle.tier}")
    low, high = band
    count = puzzle.pattern.word_count
    if low <= count <= high:
        return []
    return [
        QADefect(
            "check_word_count",
            Severity.MAJOR,
            "grid",
            f"{count} entries is outside the tier-{puzzle.tier} band of {low}-{high}",
            "Use a template from the right band rather than adjusting this grid.",
        )
    ]


def check_no_duplicate_entries(ctx: QAContext) -> list[QADefect]:
    """No answer appears twice. Duplicates read as an error even when legal."""
    seen: dict[str, str] = {}
    out: list[QADefect] = []
    for slot_id, answer in sorted(ctx.puzzle.answers.items()):
        if answer in seen:
            out.append(
                QADefect(
                    "check_no_duplicate_entries",
                    Severity.BLOCKER,
                    slot_id,
                    f"{answer} also appears at {seen[answer]}",
                    "Refill one of the two entries.",
                )
            )
        else:
            seen[answer] = slot_id
    return out


def check_theme_symmetry(ctx: QAContext) -> list[QADefect]:
    """Theme entries sit in rotationally symmetric positions.

    This is the convention a solver reads as "these are the theme entries", so
    breaking it does not just look untidy — it hides the theme.
    """
    puzzle = ctx.puzzle
    if not puzzle.theme_slots:
        raise Skip("puzzle has no theme entries")

    pattern = puzzle.pattern
    by_id = {s.id: s for s in pattern.slots}
    missing = [sid for sid in puzzle.theme_slots if sid not in by_id]
    if missing:
        return [
            QADefect(
                "check_theme_symmetry",
                Severity.BLOCKER,
                ", ".join(missing),
                "Theme slot is not in this grid",
                None,
            )
        ]

    # A slot's mirror image runs backwards: its first cell maps onto the
    # partner's last. A centred entry is its own partner and pairs with itself.
    ends = {by_id[sid].cells[-1] for sid in puzzle.theme_slots}
    unpaired = sorted(
        sid for sid in puzzle.theme_slots if pattern.mirror(by_id[sid].cells[0]) not in ends
    )
    if not unpaired:
        return []
    return [
        QADefect(
            "check_theme_symmetry",
            Severity.MAJOR,
            ", ".join(unpaired),
            f"{len(unpaired)} theme entr(y/ies) have no symmetric partner",
            "Place theme entries in mirror-image slot pairs.",
        )
    ]


def check_crossing_fairness(ctx: QAContext) -> list[QADefect]:
    """No cell where both crossing entries are obscure.

    The one rule that decides whether a hard puzzle is hard or unfair: a solver
    must always have a way in from at least one direction.
    """
    index = ctx.index
    if index is None:
        raise Skip("no lexicon supplied, so obscurity is unknown")

    puzzle = ctx.puzzle
    pattern = puzzle.pattern
    theme = set(puzzle.theme_slots)

    def obscure(slot_id: str) -> bool:
        if slot_id in theme:
            return False  # theme entries are clued to be findable, by design
        located = index.index_of.get(puzzle.answers.get(slot_id, ""))
        if located is None:
            return True  # not in the lexicon at all is as obscure as it gets
        length, i = located
        return index.entry(length, i).obscurity > OBSCURE_THRESHOLD

    out: list[QADefect] = []
    reported: set[tuple[str, str]] = set()
    for cell in pattern.white_cells:
        ids = tuple(pattern.slots[si].id for si in pattern.slots_by_cell.get(cell, ()))
        if len(ids) != 2 or ids in reported:
            continue
        if obscure(ids[0]) and obscure(ids[1]):
            reported.add(ids)
            out.append(
                QADefect(
                    "check_crossing_fairness",
                    Severity.BLOCKER,
                    f"{ids[0]} x {ids[1]}",
                    f"{puzzle.answers[ids[0]]} crosses {puzzle.answers[ids[1]]} and both are obscure",
                    "Refill one of the two so at least one entry is gettable.",
                )
            )
    return out


def check_rebus_consistency(ctx: QAContext) -> list[QADefect]:
    """A rebus cell holds the same string for its Across and its Down entry.

    Rebus support is opt-in — an empty ``rebus`` map is the normal case, not a
    missing feature — but when one is present the two entries reading through
    that cell have to agree about what is in it, or the puzzle is unsolvable in
    one direction.
    """
    puzzle = ctx.puzzle
    if not puzzle.rebus:
        raise Skip("puzzle has no rebus cells")

    pattern = puzzle.pattern
    out: list[QADefect] = []
    for cell, text in sorted(puzzle.rebus.items()):
        if pattern.is_black(cell):
            out.append(
                QADefect(
                    "check_rebus_consistency",
                    Severity.BLOCKER,
                    f"cell {pattern.coords(cell)}",
                    f"Rebus {text!r} is on a black square",
                    None,
                )
            )
            continue
        for si in pattern.slots_by_cell.get(cell, ()):
            slot = pattern.slots[si]
            answer = puzzle.answers.get(slot.id, "")
            pos = slot.cells.index(cell)
            if pos >= len(answer) or answer[pos] != text[0]:
                out.append(
                    QADefect(
                        "check_rebus_consistency",
                        Severity.BLOCKER,
                        slot.id,
                        f"Rebus {text!r} at {pattern.coords(cell)} does not match {answer!r}",
                        "Write the rebus cell's first letter into both crossing answers.",
                    )
                )
    return out


# ----------------------------------------------------------------------
# Answer and clue content checks
# ----------------------------------------------------------------------

#: Words that would end a solver's morning. Deliberately short and literal — the
#: category classifier in the editorial pass is what catches the rest, and a
#: long blocklist here would reject ordinary entries like SCUNTHORPE.
BLOCKED_ANSWERS = frozenset({"FUCK", "SHIT", "CUNT", "SPIC", "KIKE", "WOP", "CHINK", "FAGGOT"})

#: Substrings that make a clue signal an abbreviated answer.
ABBREVIATION_MARKERS = (
    "abbr",
    "for short",
    "briefly",
    "in brief",
    "initials",
    "acronym",
    ": inits",
    "shortened",
)

#: Substrings that make a clue signal a non-English answer.
FOREIGN_MARKERS = (
    "fr.",
    "sp.",
    "ger.",
    "ital.",
    "lat.",
    "in french",
    "in spanish",
    "in german",
    "in italian",
    "in latin",
    "à la",
    "señor",
)

VOWELS = frozenset("AEIOUY")


def check_profanity_and_sensitivity(ctx: QAContext) -> list[QADefect]:
    out: list[QADefect] = []
    for slot_id, answer in sorted(ctx.puzzle.answers.items()):
        if answer in BLOCKED_ANSWERS:
            out.append(
                QADefect(
                    "check_profanity_and_sensitivity",
                    Severity.BLOCKER,
                    slot_id,
                    f"{answer} is on the blocklist",
                    "Refill the entry.",
                )
            )
    for slot_id, clue in sorted(ctx.puzzle.clues.items()):
        for word in re.findall(r"[A-Za-z]+", clue.upper()):
            if word in BLOCKED_ANSWERS:
                out.append(
                    QADefect(
                        "check_profanity_and_sensitivity",
                        Severity.BLOCKER,
                        slot_id,
                        f"Clue contains {word}",
                        "Rewrite the clue.",
                    )
                )
                break
    return out


def _stem(word: str) -> str:
    """A crude suffix strip, enough to catch RUNNING in a clue for RUN.

    Not a linguistic stemmer and not trying to be: the check it serves only
    needs to know whether two words share an obvious root.
    """
    w = word.upper()
    for suffix in ("INGLY", "INGS", "ING", "EDLY", "ED", "ERS", "ER", "IES", "IED", "LY", "S"):
        if len(w) - len(suffix) >= 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def check_no_answer_word_in_clue(ctx: QAContext) -> list[QADefect]:
    """The answer, or a word sharing its root, must not appear in its own clue."""
    if not ctx.puzzle.has_clues:
        raise Skip("puzzle has no clues yet")

    out: list[QADefect] = []
    for slot_id, clue in sorted(ctx.puzzle.clues.items()):
        answer = ctx.answer(slot_id)
        if not answer:
            continue
        target = _stem(answer)
        for word in re.findall(r"[A-Za-z]{3,}", clue):
            if _stem(word) == target:
                out.append(
                    QADefect(
                        "check_no_answer_word_in_clue",
                        Severity.BLOCKER,
                        slot_id,
                        f"Clue for {answer} contains {word!r}",
                        "Reword the clue without the answer's root.",
                    )
                )
                break
    return out


def check_clue_length(ctx: QAContext) -> list[QADefect]:
    if not ctx.puzzle.has_clues:
        raise Skip("puzzle has no clues yet")

    out: list[QADefect] = []
    for slot_id, clue in sorted(ctx.puzzle.clues.items()):
        text = clue.strip()
        if not text:
            out.append(
                QADefect("check_clue_length", Severity.BLOCKER, slot_id, "Clue is empty", None)
            )
        elif len(text) > MAX_CLUE_CHARS:
            out.append(
                QADefect(
                    "check_clue_length",
                    Severity.MINOR,
                    slot_id,
                    f"Clue is {len(text)} characters (limit {MAX_CLUE_CHARS})",
                    "Tighten the wording.",
                )
            )
    return out


def _looks_abbreviated(answer: str, index: LexiconIndex) -> bool:
    """Heuristic: short, vowel-poor, and unknown to the lexicon.

    Both halves are load-bearing. Vowel-poverty alone calls CRUSH and LYNCH
    abbreviations; "unknown to the lexicon" alone calls every uncommon word one.
    Together they catch NASDQ and RSVP and leave ordinary vocabulary alone —
    which is why this check refuses to run without a lexicon rather than
    falling back to the letter shape by itself.
    """
    if len(answer) > 5 or answer in index.index_of:
        return False
    vowels = sum(1 for c in answer if c in VOWELS)
    return vowels == 0 or (len(answer) >= 4 and vowels <= 1)


def check_abbreviation_markers(ctx: QAContext) -> list[QADefect]:
    """An abbreviated answer needs a clue that says so.

    Solvers accept abbreviations; they do not accept being ambushed by one.
    """
    if not ctx.puzzle.has_clues:
        raise Skip("puzzle has no clues yet")
    if ctx.index is None:
        raise Skip("no lexicon supplied, so ordinary words cannot be told from abbreviations")

    out: list[QADefect] = []
    for slot_id, clue in sorted(ctx.puzzle.clues.items()):
        answer = ctx.answer(slot_id)
        if not _looks_abbreviated(answer, ctx.index):
            continue
        lowered = clue.lower()
        if any(marker in lowered for marker in ABBREVIATION_MARKERS):
            continue
        # A clue that is itself abbreviated signals the answer is too.
        if re.search(r"\b[A-Z]{2,}\b", clue) or "." in clue.rstrip("."):
            continue
        out.append(
            QADefect(
                "check_abbreviation_markers",
                Severity.MAJOR,
                slot_id,
                f"{answer} reads as an abbreviation but its clue does not signal one",
                'Add "Abbr." or "for short" to the clue.',
            )
        )
    return out


def check_foreign_markers(ctx: QAContext) -> list[QADefect]:
    """A non-English answer needs a clue that says which language.

    Only entries tagged ``foreign`` in the lexicon are checked. Guessing at the
    language of an untagged answer produces false positives on every English
    word with a loanword shape, which is most of them.
    """
    if not ctx.puzzle.has_clues:
        raise Skip("puzzle has no clues yet")
    if ctx.index is None:
        raise Skip("no lexicon supplied, so foreign tags are unknown")

    out: list[QADefect] = []
    for slot_id, clue in sorted(ctx.puzzle.clues.items()):
        located = ctx.index.index_of.get(ctx.answer(slot_id))
        if located is None:
            continue
        length, i = located
        if "foreign" not in ctx.index.entry(length, i).tags:
            continue
        lowered = clue.lower()
        if any(marker in lowered for marker in FOREIGN_MARKERS):
            continue
        out.append(
            QADefect(
                "check_foreign_markers",
                Severity.MAJOR,
                slot_id,
                f"{ctx.answer(slot_id)} is tagged foreign but its clue names no language",
                'Name the language, e.g. "Friend, in French".',
            )
        )
    return out


#: Inflections worth comparing, and what a mismatch means to a solver.
_AGREEMENT_SUFFIXES = (("S", "plural or third-person"), ("ING", "progressive"), ("ED", "past"))


def _inflection(word: str) -> str | None:
    for suffix, label in _AGREEMENT_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return label
    return None


def check_clue_answer_agreement(ctx: QAContext) -> list[QADefect]:
    """Clue and answer agree in part of speech, number and tense.

    **This check needs a real tagger and says so when it has none.** The
    tempting shortcut — compare the suffix of the clue's first word to the
    suffix of the answer — cannot tell a noun from an adjective, so it reports
    "Vague gesture" as disagreeing with ACRES. A check that fires on correct
    clues is worse than no check: it trains everyone downstream to ignore it.
    So the shortcut is not implemented, and with no tagger configured this
    reports as skipped, which keeps the puzzle out of ``publishable`` rather
    than passing it on evidence nobody gathered.
    """
    if not ctx.puzzle.has_clues:
        raise Skip("puzzle has no clues yet")
    if ctx.tagger is None:
        raise Skip("no part-of-speech tagger configured")

    out: list[QADefect] = []
    for slot_id, clue in sorted(ctx.puzzle.clues.items()):
        answer = ctx.answer(slot_id)
        tagged = ctx.tagger.head(clue)
        if not answer or tagged is None:
            continue
        head, pos = tagged
        head = head.upper()
        if pos not in ("NOUN", "VERB") or len(head) < 4 or len(answer) < 4:
            continue

        clue_form = _inflection(head)
        answer_form = _inflection(answer)
        if clue_form == answer_form:
            continue
        out.append(
            QADefect(
                "check_clue_answer_agreement",
                Severity.MAJOR,
                slot_id,
                f"{head.title()!r} is {clue_form or 'a base form'} but {answer} is "
                f"{answer_form or 'a base form'}",
                "Match the number and tense on both sides.",
            )
        )
    return out


# ----------------------------------------------------------------------
# The suite
# ----------------------------------------------------------------------

Check = Callable[[QAContext], list[QADefect]]

#: Order is presentation order, not dependency order — every check is
#: independent, which is what makes the broken-corpus test one puzzle per check.
CHECKS: tuple[Check, ...] = (
    check_symmetry,
    check_all_squares_checked,
    check_min_word_length,
    check_connectivity,
    check_word_count,
    check_no_duplicate_entries,
    check_no_answer_word_in_clue,
    check_theme_symmetry,
    check_clue_answer_agreement,
    check_abbreviation_markers,
    check_foreign_markers,
    check_crossing_fairness,
    check_profanity_and_sensitivity,
    check_clue_length,
    check_rebus_consistency,
)


def run_checks(
    puzzle: Puzzle,
    *,
    index: LexiconIndex | None = None,
    tagger: Tagger | None = None,
    checks: tuple[Check, ...] = CHECKS,
) -> Report:
    """Run every check. A check that cannot apply is recorded, never assumed passed."""
    ctx = QAContext(puzzle=puzzle, index=index, tagger=tagger)
    report = Report()
    for check in checks:
        try:
            report.defects.extend(check(ctx))
        except Skip as skip:
            report.skipped.append((check.__name__, str(skip)))
    return report


__all__ = [
    "ABBREVIATION_MARKERS",
    "BLOCKED_ANSWERS",
    "CHECKS",
    "FOREIGN_MARKERS",
    "MAX_CLUE_CHARS",
    "OBSCURE_THRESHOLD",
    "QAContext",
    "QADefect",
    "Report",
    "Severity",
    "Skip",
    "Tagger",
    "check_abbreviation_markers",
    "check_all_squares_checked",
    "check_clue_answer_agreement",
    "check_clue_length",
    "check_connectivity",
    "check_crossing_fairness",
    "check_foreign_markers",
    "check_min_word_length",
    "check_no_answer_word_in_clue",
    "check_no_duplicate_entries",
    "check_profanity_and_sensitivity",
    "check_rebus_consistency",
    "check_symmetry",
    "check_theme_symmetry",
    "check_word_count",
    "run_checks",
]
