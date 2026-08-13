---
id: clue-conventions
kind: core
applies_to:
  - clue_writing
  - editorial_qa
summary: >
  The clue contract every entry must satisfy — agreement, signalling, misdirection rules,
  and the per-tier difficulty mix that makes a puzzle feel like its tier.
version: 1
status: active
priority: 10
owner: puzzle
---

# Clue conventions

A clue is a promise: substitute the answer for the clue in a sentence, and it should
still be true. Everything below follows from that.

## Agreement — non-negotiable

The clue and answer must agree in:

- **Part of speech.** `Runs quickly` → `SPRINTS`, not `SPRINTING`.
- **Tense.** `Ran quickly` → `SPRINTED`.
- **Number.** `Feline` → `CAT`. `Felines` → `CATS`.

A mismatch is a defect, not a stylistic choice, and the QA pass will catch it.

## Signalling

The solver must be warned when the answer is not a plain English word:

| Answer type | Signal | Example |
|---|---|---|
| Abbreviation | `Abbr.`, or an abbreviation in the clue | `Hosp. area` → `ICU` |
| Foreign word | Name the language | `Friend, in France` → `AMI` |
| Informal | `informally`, `casually`, `slangily` | `Money, slangily` → `DOUGH` |
| Variant spelling | `Var.` | `Genie, var.` → `DJINNI` |
| Prefix / suffix | `Prefix`, `Suffix`, or a hyphen | `Prefix with -logy` → `BIO` |
| Wordplay / pun | Trailing `?` | `Peak achievement?` → `SUMMIT` |

The `?` is a contract with the solver: it means "this clue is not literal". Using it on
a straight definition is a defect. Omitting it on a pun is worse.

## Misdirection

Good misdirection exploits ambiguity that exists in English. Bad misdirection lies.

- **Good:** `Flower` for `RIVER` — a thing that flows, marked with `?`. The ambiguity is
  real and the clue is defensible after the fact.
- **Good:** capitalization at the start of a clue hides whether a word is a proper noun.
- **Bad:** a clue that is simply wrong until you know the answer.
- **Bad:** obscure trivia dressed up as wordplay.

The test: after solving, does the clue read as fair and clever, or as a trick? If a
solver would feel cheated rather than delighted, rewrite it.

## Per-tier mix

Tiers are distinguished by clue style far more than by grid difficulty. Aim for roughly:

| | Tier 1 (Warmup) | Tier 2 (Daily) | Tier 3 (Challenge) |
|---|---|---|---|
| Straight definition | ~70% | ~45% | ~25% |
| Wordplay / `?` clues | ~10% | ~30% | ~40% |
| Fill-in-the-blank | ~10% | ~10% | ~5% |
| Trivia / proper noun | ~10% | ~15% | ~30% |
| Multi-word answers | rare | some | common |

**Tier 1** clues are direct and generous. If an entry could be clued two ways, choose the
one a newer solver will get. Common crossings only.

**Tier 2** introduces misdirection and expects some cultural knowledge. The solver should
be stuck occasionally and unstuck by crossings.

**Tier 3** is dense, colloquial, and allusive. Long entries get clues that require a leap.
This is where cohort-domain knowledge is fair game — a marine-biology cohort may be clued
on `NUDIBRANCH` at a level that would be unfair generally.

## Cohort vocabulary

Personalization mostly happens here. The cohort brief tells you what this group knows and
how they read. Use it to:

- Choose *which* sense of an ambiguous word to clue.
- Reach for domain vocabulary the cohort will find satisfying rather than obscure.
- Match the register — a poetry-and-puns cohort wants double meanings; a technical
  non-fiction cohort wants precision.

Do not use it to make every clue about the cohort's interests. A puzzle where all 76 clues
reference hiking is exhausting. The theme carries the personalization; the fill clues
carry the *voice*.

## Length and form

- Keep clues under 140 characters. Most should be well under 40.
- No clue ends in a period unless it is a full sentence or an abbreviation.
- Cross-references (`See 17-Across`) are used sparingly — at most two per puzzle.
- Never repeat a distinctive word across two clues in the same puzzle.
