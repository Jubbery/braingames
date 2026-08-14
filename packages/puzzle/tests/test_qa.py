"""One deliberately broken puzzle per check, plus a clean corpus.

The two properties that matter for a QA suite are that it fires on the thing it
targets and stays silent on everything else. Each test below breaks exactly one
rule and asserts both: the right check fires, and it is the only one that does.
"""

from __future__ import annotations

import re

import pytest

from braingames_puzzle import qa
from braingames_puzzle.models import GridPattern, Puzzle

from .helpers import CORNERS_5, clean_puzzle, index_for, index_for_puzzle, open_answers


class StubTagger:
    """Tags the clue's first word, with a caller-supplied part of speech.

    Enough to exercise the agreement check without pulling an NLP model into
    the test suite; the real tagger has to satisfy the same protocol.
    """

    def __init__(self, pos: str = "NOUN") -> None:
        self.pos = pos

    def head(self, text: str) -> tuple[str, str] | None:
        words = re.findall(r"[A-Za-z]+", text)
        return (words[0], self.pos) if words else None


def defects_from(check: qa.Check, puzzle: Puzzle, **kwargs: object) -> list[qa.QADefect]:
    ctx = qa.QAContext(
        puzzle=puzzle,
        index=kwargs.get("index"),  # type: ignore[arg-type]
        tagger=kwargs.get("tagger"),  # type: ignore[arg-type]
    )
    return check(ctx)


# ----------------------------------------------------------------------
# The clean corpus
# ----------------------------------------------------------------------


def test_clean_puzzle_has_no_defects() -> None:
    puzzle = clean_puzzle()
    report = qa.run_checks(puzzle, index=index_for_puzzle(puzzle))
    assert report.defects == [], str(report)


def test_clean_puzzle_still_skips_the_inapplicable_checks() -> None:
    """A themeless, rebus-free puzzle cannot run three of the fifteen."""
    puzzle = clean_puzzle()
    report = qa.run_checks(puzzle, index=index_for_puzzle(puzzle))
    skipped = {name for name, _ in report.skipped}
    assert skipped == {
        "check_theme_symmetry",
        "check_rebus_consistency",
        "check_word_count",
        "check_clue_answer_agreement",
    }


def test_a_skipped_check_is_not_a_pass() -> None:
    """The whole point of tracking skips: an unclued puzzle is not publishable."""
    puzzle = clean_puzzle(clues={})
    report = qa.run_checks(puzzle, index=index_for_puzzle(puzzle))
    assert report.defects == []
    assert not report.publishable
    assert "check_no_answer_word_in_clue" in {name for name, _ in report.skipped}


def test_every_check_is_in_the_suite() -> None:
    """A check that exists but is not wired in protects nothing."""
    module_checks = {
        name for name in dir(qa) if name.startswith("check_") and callable(getattr(qa, name))
    }
    assert {c.__name__ for c in qa.CHECKS} == module_checks
    assert len(qa.CHECKS) == 15


# ----------------------------------------------------------------------
# Grid checks
# ----------------------------------------------------------------------


def test_symmetry_fires_on_a_lone_black_square() -> None:
    broken = clean_puzzle(pattern=GridPattern.parse("..#..\n.....\n.....\n.....\n....."))
    defects = defects_from(qa.check_symmetry, broken)
    assert len(defects) == 1
    assert defects[0].severity is qa.Severity.BLOCKER


def test_symmetry_accepts_a_mirrored_pair() -> None:
    assert defects_from(qa.check_symmetry, clean_puzzle(pattern=GridPattern.parse(CORNERS_5))) == []


def test_unchecked_square_is_caught() -> None:
    """A single white cell walled off in one direction is unreachable from it."""
    pattern = GridPattern.parse("...#.\n.....\n.....\n.....\n.#...")
    defects = defects_from(qa.check_all_squares_checked, clean_puzzle(pattern=pattern))
    assert defects and defects[0].check == "check_all_squares_checked"


def test_min_word_length_fires_on_a_two_letter_run() -> None:
    pattern = GridPattern.parse("..#..\n.....\n#...#\n.....\n..#..")
    defects = defects_from(qa.check_min_word_length, clean_puzzle(pattern=pattern))
    assert defects and defects[0].check == "check_min_word_length"


def test_connectivity_fires_on_an_island() -> None:
    pattern = GridPattern.parse("....#\n...#.\n..#..\n.#...\n#....")
    defects = defects_from(qa.check_connectivity, clean_puzzle(pattern=pattern))
    assert defects and "more than one region" in defects[0].issue


def test_word_count_band_is_enforced_on_a_full_size_grid() -> None:
    pattern = GridPattern.parse("\n".join("." * 15 for _ in range(15)))
    puzzle = clean_puzzle(pattern=pattern, answers={}, clues={}, tier=1)
    defects = defects_from(qa.check_word_count, puzzle)
    assert defects and "outside the tier-1 band" in defects[0].issue


def test_word_count_declines_to_judge_a_grid_it_has_no_band_for() -> None:
    """A 5x5 mini is not a badly-built daily crossword."""
    with pytest.raises(qa.Skip):
        defects_from(qa.check_word_count, clean_puzzle())


def test_duplicate_entries_are_caught() -> None:
    answers = open_answers()
    first, second = sorted(answers)[:2]
    answers[second] = answers[first]
    defects = defects_from(qa.check_no_duplicate_entries, clean_puzzle(answers=answers))
    assert len(defects) == 1
    assert answers[first] in defects[0].issue


def test_theme_symmetry_accepts_a_mirrored_pair() -> None:
    """In a 5x5 the first and last Across entries are each other's partners."""
    puzzle = clean_puzzle(theme_slots=("1A", "9A"))
    assert defects_from(qa.check_theme_symmetry, puzzle) == []


def test_theme_symmetry_fires_on_an_unpaired_entry() -> None:
    puzzle = clean_puzzle(theme_slots=("1A", "6A"))
    defects = defects_from(qa.check_theme_symmetry, puzzle)
    assert defects and defects[0].check == "check_theme_symmetry"


def test_theme_symmetry_skips_a_themeless_puzzle() -> None:
    with pytest.raises(qa.Skip):
        defects_from(qa.check_theme_symmetry, clean_puzzle())


def test_crossing_fairness_rejects_obscure_on_obscure() -> None:
    puzzle = clean_puzzle()
    index = index_for({answer: (40, 0.9) for answer in puzzle.answers.values()})
    defects = defects_from(qa.check_crossing_fairness, puzzle, index=index)
    assert defects
    assert all(d.severity is qa.Severity.BLOCKER for d in defects)


def test_crossing_fairness_allows_obscure_crossing_familiar() -> None:
    """One obscure entry is a challenge; two crossing is a coin flip."""
    puzzle = clean_puzzle()
    obscure_id = sorted(puzzle.answers)[0]
    scores = {
        answer: ((40, 0.9) if slot_id == obscure_id else (80, 0.1))
        for slot_id, answer in puzzle.answers.items()
    }
    assert defects_from(qa.check_crossing_fairness, puzzle, index=index_for(scores)) == []


def test_crossing_fairness_skips_without_a_lexicon() -> None:
    with pytest.raises(qa.Skip):
        defects_from(qa.check_crossing_fairness, clean_puzzle())


def test_rebus_must_agree_with_both_crossing_answers() -> None:
    puzzle = clean_puzzle(rebus={0: "ZZ"})
    defects = defects_from(qa.check_rebus_consistency, puzzle)
    assert defects and all(d.severity is qa.Severity.BLOCKER for d in defects)


def test_rebus_matching_the_grid_passes() -> None:
    puzzle = clean_puzzle()
    first_letter = puzzle.answers["1A"][0]
    puzzle.rebus = {0: first_letter + "OO"}
    assert defects_from(qa.check_rebus_consistency, puzzle) == []


# ----------------------------------------------------------------------
# Content and clue checks
# ----------------------------------------------------------------------


def test_blocklisted_answer_is_rejected() -> None:
    answers = open_answers()
    answers["1A"] = "SHIT"
    defects = defects_from(qa.check_profanity_and_sensitivity, clean_puzzle(answers=answers))
    assert defects and defects[0].severity is qa.Severity.BLOCKER


def test_blocklisted_word_in_a_clue_is_rejected() -> None:
    puzzle = clean_puzzle()
    puzzle.clues["1A"] = "Complete shit, informally"
    defects = defects_from(qa.check_profanity_and_sensitivity, puzzle)
    assert defects and "Clue contains" in defects[0].issue


def test_answer_root_in_its_own_clue_is_rejected() -> None:
    puzzle = clean_puzzle()
    puzzle.answers["1A"] = "SHARP"
    puzzle.clues["1A"] = "Sharply pointed"
    defects = defects_from(qa.check_no_answer_word_in_clue, puzzle)
    assert defects and defects[0].severity is qa.Severity.BLOCKER


def test_a_synonym_clue_is_fine() -> None:
    puzzle = clean_puzzle()
    puzzle.answers["1A"] = "SHARP"
    puzzle.clues["1A"] = "Keen-edged"
    assert defects_from(qa.check_no_answer_word_in_clue, puzzle) == []


def test_empty_and_overlong_clues_are_caught() -> None:
    puzzle = clean_puzzle()
    puzzle.clues["1A"] = ""
    puzzle.clues["2D"] = "x" * (qa.MAX_CLUE_CHARS + 1)
    defects = {d.location: d for d in defects_from(qa.check_clue_length, puzzle)}
    assert defects["1A"].severity is qa.Severity.BLOCKER
    assert defects["2D"].severity is qa.Severity.MINOR


def test_abbreviation_without_a_marker_is_flagged() -> None:
    puzzle = clean_puzzle()
    index = index_for_puzzle(puzzle)  # knows the real answers, not NASDQ
    puzzle.answers["1A"] = "NASDQ"
    puzzle.clues["1A"] = "Stock exchange"
    defects = defects_from(qa.check_abbreviation_markers, puzzle, index=index)
    assert [d.location for d in defects] == ["1A"]


def test_abbreviation_with_a_marker_passes() -> None:
    puzzle = clean_puzzle()
    index = index_for_puzzle(puzzle)
    puzzle.answers["1A"] = "NASDQ"
    puzzle.clues["1A"] = "Stock exchange, for short"
    assert defects_from(qa.check_abbreviation_markers, puzzle, index=index) == []


def test_abbreviation_check_refuses_to_guess_without_a_lexicon() -> None:
    """Vowel-poverty alone calls CRUSH an abbreviation, so it is not enough."""
    with pytest.raises(qa.Skip):
        defects_from(qa.check_abbreviation_markers, clean_puzzle())


def test_ordinary_words_are_not_mistaken_for_abbreviations() -> None:
    puzzle = clean_puzzle()
    index = index_for_puzzle(puzzle)
    assert defects_from(qa.check_abbreviation_markers, puzzle, index=index) == []


def test_foreign_answer_needs_its_language_named() -> None:
    puzzle = clean_puzzle()
    puzzle.answers["1A"] = "AMIGO"
    puzzle.clues["1A"] = "Friend"
    index = index_for({"AMIGO": (65, 0.2)}, tags={"AMIGO": {"foreign"}})
    defects = defects_from(qa.check_foreign_markers, puzzle, index=index)
    assert defects and defects[0].check == "check_foreign_markers"

    puzzle.clues["1A"] = "Friend, in Spanish"
    assert defects_from(qa.check_foreign_markers, puzzle, index=index) == []


def test_untagged_answers_are_not_guessed_at() -> None:
    puzzle = clean_puzzle()
    puzzle.answers["1A"] = "AMIGO"
    puzzle.clues["1A"] = "Friend"
    index = index_for({"AMIGO": (65, 0.2)})
    assert defects_from(qa.check_foreign_markers, puzzle, index=index) == []


def test_number_and_tense_disagreement_is_flagged() -> None:
    puzzle = clean_puzzle(answers={"1A": "SPRINTS"}, clues={"1A": "Sprinted quickly"})
    defects = defects_from(qa.check_clue_answer_agreement, puzzle, tagger=StubTagger("VERB"))
    assert defects and defects[0].severity is qa.Severity.MAJOR


def test_matching_forms_agree() -> None:
    puzzle = clean_puzzle(answers={"1A": "DASHED"}, clues={"1A": "Bolted quickly"})
    assert defects_from(qa.check_clue_answer_agreement, puzzle, tagger=StubTagger("VERB")) == []


def test_agreement_ignores_a_clue_whose_head_is_not_a_noun_or_verb() -> None:
    """The false positive that killed the suffix-only version: an adjectival
    clue head tells you nothing about the answer's number."""
    puzzle = clean_puzzle(answers={"1A": "ACRES"}, clues={"1A": "Vague gesture"})
    assert defects_from(qa.check_clue_answer_agreement, puzzle, tagger=StubTagger("ADJ")) == []


def test_agreement_skips_without_a_tagger() -> None:
    with pytest.raises(qa.Skip):
        defects_from(qa.check_clue_answer_agreement, clean_puzzle())
