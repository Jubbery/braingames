"""Grid legality, theme placement, and the template generator."""

from __future__ import annotations

from braingames_puzzle import templates as tpl
from braingames_puzzle.models import GridPattern

LEGAL_5 = GridPattern.parse("""
.....
.....
.....
.....
.....
""")


def kinds(pattern: GridPattern, **kwargs: object) -> set[tpl.Violation]:
    return {d.kind for d in tpl.validate(pattern, **kwargs)}  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def test_an_open_square_is_legal() -> None:
    assert tpl.validate(LEGAL_5) == []
    assert tpl.is_legal(LEGAL_5)


def test_asymmetry_is_caught() -> None:
    assert tpl.Violation.ASYMMETRIC in kinds(GridPattern.parse("..#..\n.....\n.....\n.....\n....."))


def test_a_short_entry_is_caught() -> None:
    assert tpl.Violation.SHORT_ENTRY in kinds(
        GridPattern.parse("..#..\n.....\n#...#\n.....\n..#..")
    )


def test_an_unchecked_cell_is_caught() -> None:
    assert tpl.Violation.UNCHECKED_CELL in kinds(
        GridPattern.parse("...#.\n.....\n.....\n.....\n.#...")
    )


def test_a_disconnected_grid_is_caught() -> None:
    assert tpl.Violation.DISCONNECTED in kinds(
        GridPattern.parse("....#\n...#.\n..#..\n.#...\n#....")
    )


def test_an_empty_grid_is_caught() -> None:
    assert tpl.Violation.EMPTY in kinds(GridPattern.parse("#####\n#####\n#####\n#####\n#####"))


def test_the_word_count_band_is_only_checked_when_given() -> None:
    assert tpl.Violation.WORD_COUNT not in kinds(LEGAL_5)
    assert tpl.Violation.WORD_COUNT in kinds(LEGAL_5, band=(70, 80))


# ----------------------------------------------------------------------
# Symmetry and theme placement
# ----------------------------------------------------------------------


def test_symmetric_partner_pairs_first_and_last_rows() -> None:
    first = next(s for s in LEGAL_5.slots if s.id == "1A")
    partner = tpl.symmetric_partner(LEGAL_5, first)
    assert partner is not None
    assert partner.row == LEGAL_5.size - 1


def test_a_centred_entry_is_its_own_partner() -> None:
    middle = next(s for s in LEGAL_5.slots if s.row == 2 and s.direction.letter == "A")
    assert tpl.symmetric_partner(LEGAL_5, middle) == middle


def test_theme_slots_are_across_and_long_enough() -> None:
    slots = tpl.theme_slots(LEGAL_5, min_length=5)
    assert all(s.direction.letter == "A" and s.length >= 5 for s in slots)
    assert len(slots) == 5


def test_an_even_theme_set_is_placed_in_mirror_pairs() -> None:
    placement = tpl.place_theme(LEGAL_5, [5, 5])
    assert placement is not None
    a, b = (next(s for s in LEGAL_5.slots if s.id == placement[i]) for i in (0, 1))
    assert LEGAL_5.mirror(a.cells[0]) == b.cells[-1]


def test_an_odd_theme_entry_needs_the_centre() -> None:
    placement = tpl.place_theme(LEGAL_5, [5, 5, 5])
    assert placement is not None
    centred = next(s for s in LEGAL_5.slots if s.id == placement[2])
    assert tpl.symmetric_partner(LEGAL_5, centred) == centred


def test_an_unplaceable_theme_set_returns_none() -> None:
    """Two odd entries out cannot both take the one centred slot."""
    assert tpl.place_theme(LEGAL_5, [5, 5, 5, 5, 5, 5]) is None


def test_an_empty_theme_places_trivially() -> None:
    assert tpl.place_theme(LEGAL_5, []) == {}


def test_a_theme_longer_than_any_slot_is_refused() -> None:
    assert tpl.place_theme(LEGAL_5, [9, 9]) is None


# ----------------------------------------------------------------------
# Template records and generation
# ----------------------------------------------------------------------


def test_describe_records_what_the_solver_needs() -> None:
    template = tpl.describe(LEGAL_5, band=3)
    assert template.size == 5
    assert template.word_count == LEGAL_5.word_count
    assert template.max_slot_length == 5
    assert template.difficulty_band == 3
    assert template.pattern == LEGAL_5.to_text(newlines=False)


def test_describe_is_deterministic() -> None:
    assert tpl.describe(LEGAL_5, band=1).id == tpl.describe(LEGAL_5, band=1).id


def test_generated_templates_are_legal_and_in_band() -> None:
    """The generator's whole job: everything it emits is usable forever after."""
    library = tpl.generate_library(size=15, per_band=3, seed=11)
    assert library

    for template in library:
        pattern = GridPattern.parse(template.pattern)
        band = tpl.WORD_COUNT_BANDS[template.difficulty_band]
        assert tpl.validate(pattern, band=band) == [], template.id


def test_generated_templates_are_unique() -> None:
    library = tpl.generate_library(size=15, per_band=3, seed=11)
    assert len({t.pattern for t in library}) == len(library)


def test_generation_is_reproducible() -> None:
    first = tpl.generate_library(size=15, per_band=2, seed=5)
    second = tpl.generate_library(size=15, per_band=2, seed=5)
    assert [t.id for t in first] == [t.id for t in second]


def test_every_band_is_represented() -> None:
    library = tpl.generate_library(size=15, per_band=3, seed=11)
    assert {t.difficulty_band for t in library} == set(tpl.WORD_COUNT_BANDS)


def test_generated_templates_offer_theme_slots() -> None:
    """A template with nowhere to put a theme entry cannot carry a themed puzzle."""
    for template in tpl.generate_library(size=15, per_band=3, seed=11):
        assert template.theme_slots, template.id


# ----------------------------------------------------------------------
# Theme capacity
# ----------------------------------------------------------------------


def test_theme_capacity_counts_pairs_and_centred_slots() -> None:
    capacity = tpl.theme_capacity(LEGAL_5, min_length=5)
    pairs, singles = capacity[5]
    assert pairs == 2  # rows 0/4 and rows 1/3
    assert singles == 1  # the centre row is its own partner


def test_capacity_and_placement_agree() -> None:
    """The contract that makes capacity useful: a shape it reports as
    available must actually place, and one it does not must not."""
    for pattern in (LEGAL_5, GridPattern.parse("\n".join("." * 15 for _ in range(15)))):
        capacity = tpl.theme_capacity(pattern)
        for length, (pairs, _singles) in capacity.items():
            if pairs >= 1:
                assert tpl.place_theme(pattern, [length, length]) is not None

        absent = next(
            (n for n in range(7, pattern.size + 1) if capacity.get(n, (0, 0))[0] == 0), None
        )
        if absent is not None:
            assert tpl.place_theme(pattern, [absent, absent]) is None


def test_generated_templates_publish_their_capacity() -> None:
    """P3's ideation stage is handed these shapes so it proposes a theme the
    grid can hold, rather than one discovered to be unplaceable later."""
    for template in tpl.generate_library(size=15, per_band=2, seed=11):
        assert template.theme_capacity
        assert any(pairs >= 1 for pairs, _ in template.theme_capacity.values()), template.id
