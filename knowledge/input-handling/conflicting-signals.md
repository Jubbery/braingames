---
id: conflicting-signals
kind: input_handling
applies_to:
  - profile_classification
  - theme_ideation
summary: >
  Use when profile fields disagree — an interest that overlaps an avoided topic, a
  difficulty preference contradicted by solve history, or interests pulling in opposite
  directions.
triggers:
  always: true
version: 1
status: active
priority: 40
owner: personalization
---

# Conflicting signals

Profiles contradict themselves routinely. People are not consistent, and the resolution
rules matter because getting them wrong produces puzzles that feel like they were made by
something that was not listening.

## avoid_topics beats interests, always

A player listing `history` and avoiding `war` has not made a mistake. They want history
themes that are not about war, and those are abundant — trade routes, cartography, the
history of food, writing systems, epidemiology of ideas.

**Do not** drop the interest because it partially overlaps an exclusion. **Do not** treat
the exclusion as a soft preference. Route around it.

The exclusion applies to the *theme*, not to every word in the grid. `WAR` may appear in
the fill of a puzzle for a player who avoids war themes, clued neutrally. A theme *about*
war may not. This is the same distinction drawn in `sensitive-topics`.

## Interests that pull apart

A profile with `music.classical`, `tech.gamedev`, and `outdoors.climbing` is not
confused — it is a person. Do not average them into mush.

- Rotate. Different days lean on different interests.
- Look for genuine intersections when they exist and are not forced. Climbing and game
  design share vocabulary around routes, problems, and beta. That is a real theme.
- Never construct a theme whose only coherence is that it touches all of the player's
  interests. Those read as generated, because they are.

## Stated difficulty vs. observed

The profile says Tier 1; the player solves Tier 3 unassisted in eight minutes.

Observed behaviour wins for *suggestions*, the stated preference wins for *defaults*, and
the player always gets to open any tier. Suggest the change and explain the evidence:

> You've solved the last eight Daily puzzles unassisted, well under the group median.
> Want to try Challenge?

Never silently reassign. A player who set Tier 1 deliberately — because they solve on a
commute, or because they are learning — is not wrong, and having the app decide otherwise
without asking is the kind of small betrayal that loses trust.

## Wordplay affinity vs. reading genres

`wordplay_affinity: straightforward` with `reading_genres: [poetry]` is a real
combination — someone who loves language and dislikes puns. Trust the explicit answer.
`wordplay_affinity` is a direct question about clue style; reading genres are an indirect
signal. Direct beats indirect.

## Precedence, summarised

1. `avoid_topics` — absolute, over everything.
2. Explicit preference fields — `wordplay_affinity`, `baseline_difficulty`.
3. Chosen interest tags.
4. Observed solve behaviour — informs suggestions, never overrides a stated preference
   silently.
5. Inferred signals — job field, free-text flavour, locale.
