---
id: sparse-profile
kind: input_handling
applies_to:
  - profile_classification
  - theme_ideation
summary: >
  Use when a profile has few or no interests — a skipped onboarding, two tags, or nothing
  but a job field. Covers widening strategy and why a forced niche theme is the wrong fix.
triggers:
  always: true
version: 1
status: active
priority: 40
owner: personalization
---

# Sparse profiles

Onboarding is skippable at every step, so plenty of profiles arrive nearly empty. A sparse
profile is a normal state, not an error, and it must produce a good crossword.

## The failure to avoid

The tempting move with two tags is to lean on them hard — a player who listed only
`food.baking` gets baking themes every day. That is worse than a general puzzle. Two tags
are not enough evidence to build a personality on, and the repetition is obvious within a
week.

**Sparse input means widen, not concentrate.**

## Strategy by sparsity

**Nothing at all** (skipped entirely). General-audience cohort. Broad themes with wide
cultural range. This is a good crossword and needs no apology — most published crosswords
are exactly this.

**One or two tags.** Widen to the parent domain. `food.baking` alone becomes a food-and-
drink cohort, not a baking cohort. Use the tag for *fill vocabulary* — a slightly higher
weighting on baking terms in the lexicon — and let the theme range across the domain.

**Tags but no reading or writing signal.** Default to a middle register: some wordplay,
some straight cluing, no strong assumptions about vocabulary depth. The reading and
writing answers are the main input to clue voice, so without them, do not commit to one.

**Job field only.** Weakest useful signal. Use it for fill vocabulary and nothing else. Do
not theme on someone's job — see `freetext-normalization`.

**Difficulty only.** Perfectly serviceable. Tier calibration is independent of theme
personalization, and a player who told us nothing but "I want hard puzzles" has told us
something genuinely useful.

## Cohort assignment

Sparse profiles cluster poorly by construction — there is not enough vector to cluster.
They go to the general cohort rather than being forced into a niche one on thin evidence.
The general cohort is large, well-reviewed, and its puzzles double as the evergreen pool,
so this is a good place to land.

## Growing out of it

Sparse profiles are not permanent. The signals that fill them in:

- The `more like this` / `less like this` control on the post-solve card.
- Solve-rate patterns across themes, which reveal preference without a form.
- A prompt to add interests, shown after roughly ten solves — late enough that the player
  has a reason to care, early enough to matter.

Do not prompt during the first session. A player who skipped onboarding skipped it on
purpose.
