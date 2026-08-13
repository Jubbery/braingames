---
id: construction-rules
kind: core
applies_to:
  - theme_ideation
  - editorial_qa
summary: >
  The non-negotiable legality and quality rules every Braingames crossword must satisfy:
  symmetry, interlock, word counts, theme placement, and fill quality gates.
version: 1
status: active
priority: 10
owner: puzzle
---

# Construction rules

These are hard constraints. A puzzle that violates any of them is rejected before a
player ever sees it, so producing one wastes a generation cycle. Design within them.

## Grid legality

Every grid, every tier, without exception:

1. **180° rotational symmetry.** If `(r, c)` is a black square, so is `(14-r, 14-c)` on a
   15×15. Symmetry is checked mechanically; there is no "close enough".
2. **Every square is checked.** Each white square belongs to both an Across entry and a
   Down entry. Unchecked squares are the most common amateur error and are never
   acceptable.
3. **Minimum entry length is 3.** No two-letter entries anywhere.
4. **White space is connected.** The whole grid must be solvable as one region — no
   isolated pockets.
5. **Word count within band.** Tier 1: 74–78. Tier 2: 72–78. Tier 3: 68–72.

You do not choose the black-square pattern. Grids are selected from a library of
pre-validated templates whose legality is guaranteed at ingest. Your job is to supply
theme entries whose *lengths* can be placed symmetrically in one of them.

## Theme entry placement

This is the constraint that most often makes an otherwise good theme unusable, so check
it before proposing entries:

- Theme entries must be placeable **symmetrically**. In practice that means entries pair
  up by equal length (a 13 with a 13, an 11 with an 11), with at most one odd entry that
  sits on the centre row.
- A revealer, when present, is usually the last theme entry and often sits in the
  bottom-centre. It counts toward the symmetry budget.
- Maximum entry length is the grid width — 15 on a standard puzzle.
- Theme square budget: Tier 1, 36–52 squares. Tier 2, 30–48. Tier 3 is themeless.

**Worked example.** `SLIPPERY SLOPE` (13) and `NARROW PATH` (10) cannot pair. Either
find a 13 to match `SLIPPERYSLOPE`, or replace it with a 10 to match `NARROWPATH`.
Count letters, not words, and ignore spaces and punctuation entirely — `SLIPPERY SLOPE`
enters the grid as the 13 letters `SLIPPERYSLOPE`.

## Theme quality

A theme is a *mechanism*, not a topic. "Birds" is a topic. "Common phrases whose first
word is also a bird" is a mechanism.

- Every theme entry must embody the mechanism the same way. If three entries hide a bird
  at the start and the fourth hides one in the middle, the theme is broken.
- The mechanism must be discoverable from the entries plus the clues. A solver who
  finishes should be able to say what the trick was.
- Tier 1 themes may state the trick outright via a revealer. Tier 2 themes should let
  the solver find it. Tier 3 is themeless.
- Theme entries should be in-language phrases a person would actually say, not
  constructed strings.

## Fill quality gates

The fill is produced by a solver, not by you, but these thresholds decide whether your
theme entries are usable in practice. Themes requiring rare letter patterns in long
entries force bad fill.

- Mean word score ≥ 55 (Tier 1), ≥ 50 (Tier 2), ≥ 45 (Tier 3).
- No entry scoring below 25.
- At most three entries scoring below 40.
- **No two obscure entries may cross.** This is the single most important fairness rule.
  A solver must always be able to infer a letter from at least one of its two entries.

## Duplication

- No entry appears twice in the same puzzle.
- No entry is a substring of another entry in the same puzzle.
- No substantial word in a clue appears in its own answer, including as a stem
  (`RUNNING` in a clue for `RUN` is a violation).

## What good looks like

A finished puzzle where the theme is coherent, the trick is discoverable, the fill is
clean enough that no entry makes a solver wince, and every crossing is fair. If any of
those is missing, personalization is worthless — it is a gimmick attached to a bad
crossword.
