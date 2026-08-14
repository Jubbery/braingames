"""Fill solver: search correctness, theme seeding, and the quality gates.

These run against small hand-built lexicons rather than the real one, so a
failure points at the search rather than at the word list.
"""

from __future__ import annotations

from braingames_puzzle.lexicon import LexiconEntry, LexiconIndex
from braingames_puzzle.models import Fill, GridPattern
from braingames_puzzle.solver import (
    FillConfig,
    _Search,
    evaluate,
    fill_grid,
    fill_with_retries,
)

TINY = GridPattern.parse("...\n...\n...")

#: A genuine 3x3 double word square: six distinct words, real in both
#: directions. A *symmetric* square will not do — its Across and Down entries
#: are the same six words, and the solver rightly refuses to repeat an entry.
SQUARE = ("ACT", "SAW", "PRO", "ASP", "CAR", "TWO")
SQUARE_FILL = {"1A": "ACT", "4A": "SAW", "5A": "PRO", "1D": "ASP", "2D": "CAR", "3D": "TWO"}


def index_of(*words: str, score: int = 80, obscurity: float = 0.1) -> LexiconIndex:
    return LexiconIndex(LexiconEntry(w, w, score, obscurity, "test") for w in words)


def test_solves_a_word_square() -> None:
    result = fill_grid(TINY, index_of(*SQUARE), config=FillConfig(tier=3, seed=1))
    assert result.ok, result.reason
    assert result.fill is not None
    assert result.fill.is_complete
    assert result.fill.answers == SQUARE_FILL


def test_reports_an_unfillable_grid_rather_than_hanging() -> None:
    """Three words that share no crossing letters cannot tile a 3x3."""
    index = index_of("AAA", "BBB", "CCC")
    result = fill_grid(TINY, index, config=FillConfig(time_budget_s=2.0, seed=1))
    assert not result.ok
    assert result.reason in {"exhausted", "timeout", "node_limit"}


def test_no_word_is_used_twice() -> None:
    result = fill_grid(TINY, index_of(*SQUARE), config=FillConfig(seed=1))
    assert result.ok
    assert result.fill is not None
    answers = list(result.fill.answers.values())
    assert len(answers) == len(set(answers))


# ----------------------------------------------------------------------
# Theme seeding
# ----------------------------------------------------------------------


def test_theme_entry_survives_into_the_fill() -> None:
    result = fill_grid(TINY, index_of(*SQUARE), theme={"1A": "ACT"}, config=FillConfig(seed=1))
    assert result.ok
    assert result.fill is not None
    assert result.fill.answers["1A"] == "ACT"


def test_a_theme_word_absent_from_the_lexicon_is_still_placed() -> None:
    """The whole point of a theme is entries the word list has never heard of."""
    index = index_of(*(w for w in SQUARE if w != "ACT"))
    result = fill_grid(TINY, index, theme={"1A": "ACT"}, config=FillConfig(seed=1))
    assert result.ok, result.reason
    assert result.fill is not None
    assert result.fill.answers["1A"] == "ACT"


def test_a_theme_slot_that_does_not_exist_is_named() -> None:
    result = fill_grid(TINY, index_of(*SQUARE), theme={"99A": "ACT"})
    assert not result.ok
    assert "99A" in result.reason


def test_a_theme_answer_of_the_wrong_length_is_named() -> None:
    result = fill_grid(TINY, index_of(*SQUARE), theme={"1A": "TOOLONG"})
    assert not result.ok
    assert "7 letters" in result.reason


def test_an_impossible_theme_is_not_retried() -> None:
    """Reshuffling cannot fix a theme that does not fit, so it should not try."""
    result = fill_grid(TINY, index_of(*SQUARE), theme={"1A": "TOOLONG"})
    assert result.attempts == 1


# ----------------------------------------------------------------------
# The score floor and its safety net
# ----------------------------------------------------------------------


def test_score_floor_excludes_weak_words() -> None:
    """With plenty of words above the floor, the floor is obeyed exactly."""
    strong = [LexiconEntry(w, w, 90, 0.1, "t") for w in _many(60)]
    weak = [LexiconEntry(w, w, 30, 0.1, "t") for w in _many(60, start=60)]
    search = _Search(TINY, LexiconIndex(strong + weak), {}, FillConfig(min_score=50))
    assert search.domains[0].bit_count() == 60


def test_a_starved_length_relaxes_its_floor_instead_of_dying() -> None:
    """The bug this guards: a floor that empties a whole length makes MRV pick
    those slots first and the search dies at the root with no clue why."""
    index = index_of(*_many(80), score=30)
    config = FillConfig(min_score=50)
    search = _Search(TINY, index, {}, config)
    assert search.domains[0].bit_count() == config.max_candidates


def test_the_relaxation_stops_at_what_the_search_would_examine() -> None:
    """Widening further would quietly cancel the raised floor that
    fill_with_retries uses to lift quality."""
    index = index_of(*_many(200), score=30)
    config = FillConfig(min_score=50, max_candidates=40)
    search = _Search(TINY, index, {}, config)
    assert search.domains[0].bit_count() == 40


def test_the_relaxation_never_invents_candidates() -> None:
    search = _Search(TINY, index_of("CAT", "ARE", "TEN", score=30), {}, FillConfig(min_score=50))
    assert search.domains[0].bit_count() == 3


# ----------------------------------------------------------------------
# Quality gates
# ----------------------------------------------------------------------


def test_obscure_crossing_obscure_is_rejected() -> None:
    """The fairness rule, stated as a test: two unguessable entries must not
    decide a shared letter."""
    index = index_of(*SQUARE, obscurity=0.95)
    fill = Fill(pattern=TINY, answers=dict(SQUARE_FILL))
    quality = evaluate(fill, index, tier=3)
    assert quality.obscure_crossings
    assert not quality.passes
    assert any("obscure-on-obscure" in f for f in quality.failures)


def test_one_obscure_entry_crossing_familiar_ones_is_allowed() -> None:
    index = LexiconIndex(LexiconEntry(w, w, 80, 0.95 if w == "ACT" else 0.1, "t") for w in SQUARE)
    fill = Fill(pattern=TINY, answers=dict(SQUARE_FILL))
    assert not evaluate(fill, index, tier=3).obscure_crossings


def test_theme_entries_are_exempt_from_the_gates() -> None:
    """Theme answers are chosen deliberately and are usually absent from the
    word list; judging them as fill would penalise the point of the puzzle."""
    index = index_of(*(w for w in SQUARE if w != "ACT"))
    fill = Fill(pattern=TINY, answers={**SQUARE_FILL, "1A": "ZZZ"})
    assert "ZZZ" not in str(evaluate(fill, index, tier=3, theme_slots={"1A"}).failures)


def test_a_mean_below_the_tier_floor_fails() -> None:
    fill = Fill(pattern=TINY, answers=dict(SQUARE_FILL))
    quality = evaluate(fill, index_of(*SQUARE, score=30), tier=1)
    assert any("below the tier-1 floor" in f for f in quality.failures)


def test_an_entry_the_lexicon_does_not_know_is_reported() -> None:
    fill = Fill(pattern=TINY, answers={"1A": "QQQ"})
    failures = evaluate(fill, index_of(*SQUARE), tier=3).failures
    assert any("not in the lexicon" in f for f in failures)


def test_retries_raise_the_floor_rather_than_reshuffling() -> None:
    """A fill that completes but scores badly settled for junk it was allowed
    to use; removing the junk is the fix, not another shuffle."""
    index = LexiconIndex(
        [LexiconEntry(w, w, 30, 0.1, "t") for w in _many(200)]
        + [LexiconEntry(w, w, 90, 0.1, "t") for w in SQUARE]
    )
    result = fill_with_retries(TINY, index, config=FillConfig(tier=1, seed=1))
    assert result.ok
    assert result.quality is not None
    assert result.quality.mean_score >= 55


def _many(count: int, start: int = 0) -> list[str]:
    """Distinct three-letter strings, for tests about domain sizes."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = []
    for n in range(start, start + count):
        out.append(letters[n // 676 % 26] + letters[n // 26 % 26] + letters[n % 26])
    return out
