"""Lexicon scoring, storage and the solver's index."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from braingames_puzzle import lexicon as lex


def test_normalize_keeps_a_display_form() -> None:
    assert lex.normalize("slippery slope") == ("SLIPPERYSLOPE", "SLIPPERY SLOPE")
    assert lex.normalize("well-worn") == ("WELLWORN", "WELL-WORN")


@pytest.mark.parametrize("raw", ["", "  ", "ab", "12345", "a" * 30])
def test_normalize_rejects_the_unusable(raw: str) -> None:
    assert lex.normalize(raw) is None


def test_score_is_attestation_only() -> None:
    """Length must not move the score. It used to, and that removed whole
    lengths from the solver's reach while changing no candidate ordering."""
    entries = _ingest({"words": ["cat", "extraordinarily"]})
    scores = {e.word: e.score for e in entries}
    assert scores["CAT"] == scores["EXTRAORDINARILY"] == lex.SCORE_VALID


def test_obscurity_rises_with_length_and_rare_letters() -> None:
    assert lex.obscurity_for(lex.SCORE_VALID, "CRANE") < lex.obscurity_for(
        lex.SCORE_VALID, "TRAILHEAD"
    )
    assert lex.obscurity_for(lex.SCORE_VALID, "RETAINS") < lex.obscurity_for(
        lex.SCORE_VALID, "ZYZZYVA"
    )


def test_common_words_stay_unobscure_however_long() -> None:
    assert lex.obscurity_for(lex.SCORE_TOP, "INFORMATION") < 0.2


def test_ranked_source_scores_by_position() -> None:
    # Digits are stripped by normalize, so the words have to differ in letters.
    words = [f"{a}{b}{c}" for a in "abcde" for b in "abcde" for c in "abcd"]
    entries = _ingest({"ranked": words}, ranked={"ranked"})
    by_word = {e.word: e.score for e in entries}
    assert by_word[words[0].upper()] == lex.SCORE_TOP
    assert by_word[words[-1].upper()] < by_word[words[0].upper()]


def test_highest_score_wins_across_sources(tmp_path: Path) -> None:
    common = tmp_path / "common.txt"
    common.write_text("shared\n")
    rare = tmp_path / "rare.txt"
    rare.write_text("shared\n")

    entries = lex.ingest(
        [
            lex.Source("rare", rare, flat_score=lex.SCORE_VALID),
            lex.Source("common", common, flat_score=lex.SCORE_FAMILIAR),
        ]
    )
    assert [e.score for e in entries] == [lex.SCORE_FAMILIAR]
    assert entries[0].source == "common"


def test_round_trip_through_gzip(tmp_path: Path) -> None:
    entries = [
        lex.LexiconEntry("ONTHEROCKS", "ON THE ROCKS", 70, 0.2, "test", frozenset({"phrase"}))
    ]
    path = tmp_path / "lex.tsv.gz"
    assert lex.save(entries, path) == 1
    assert lex.load(path) == entries


def test_load_rejects_a_foreign_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.tsv.gz"
    with gzip.open(path, "wt") as fh:
        fh.write("not our header\nrubbish\n")
    with pytest.raises(ValueError, match="Unexpected lexicon header"):
        lex.load(path)


def test_load_names_the_build_command_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="lexicon ingest"):
        lex.load(tmp_path / "absent.tsv.gz")


# ----------------------------------------------------------------------
# Bitsets and the index
# ----------------------------------------------------------------------


def test_bits_round_trip() -> None:
    indices = [0, 3, 7, 8, 63, 64, 200]
    mask = lex.bits_from_indices(indices, 256)
    assert list(lex.iter_bits(mask)) == indices


def test_index_orders_words_by_score_within_a_length() -> None:
    """The solver relies on this: walking a domain low bit first is best first."""
    index = lex.LexiconIndex(
        [
            lex.LexiconEntry("CAT", "CAT", 40, 0.5, "t"),
            lex.LexiconEntry("DOG", "DOG", 95, 0.0, "t"),
            lex.LexiconEntry("EMU", "EMU", 65, 0.2, "t"),
        ]
    )
    assert index.words[3] == ["DOG", "EMU", "CAT"]
    assert index.scores[3] == [95, 65, 40]


def test_masks_select_words_by_letter_and_position() -> None:
    index = lex.LexiconIndex(
        [
            lex.LexiconEntry("CAT", "CAT", 90, 0.0, "t"),
            lex.LexiconEntry("CAR", "CAR", 80, 0.0, "t"),
            lex.LexiconEntry("BAT", "BAT", 70, 0.0, "t"),
        ]
    )
    starts_c = index.mask_for(3, 0, "C")
    ends_t = index.mask_for(3, 2, "T")
    both = [index.word(3, i) for i in lex.iter_bits(starts_c & ends_t)]
    assert both == ["CAT"]


def test_index_reports_what_it_holds() -> None:
    index = lex.LexiconIndex(
        [
            lex.LexiconEntry("CAT", "CAT", 90, 0.0, "t"),
            lex.LexiconEntry("HORSE", "HORSE", 90, 0.0, "t"),
        ]
    )
    assert index.size == 2
    assert index.lengths == [3, 5]
    assert index.has_length(3) and not index.has_length(4)
    assert index.index_of["HORSE"] == (5, 0)


def _ingest(files: dict[str, list[str]], ranked: set[str] | None = None) -> list[lex.LexiconEntry]:
    import tempfile

    ranked = ranked or set()
    sources = []
    tmp = Path(tempfile.mkdtemp())
    for name, words in files.items():
        path = tmp / f"{name}.txt"
        path.write_text("\n".join(words))
        sources.append(lex.Source(name, path, ranked=name in ranked))
    return lex.ingest(sources)
