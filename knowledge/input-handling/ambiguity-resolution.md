---
id: ambiguity-resolution
kind: input_handling
applies_to:
  - profile_classification
summary: >
  Use when a free-text interest has more than one plausible meaning. Resolves by
  co-occurring tags then locale, and says when to return low confidence instead of guessing.
triggers:
  always: true
version: 1
status: active
priority: 35
owner: personalization
---

# Resolving ambiguous interests

Some words mean different things to different people, and picking wrong produces a puzzle
about the wrong sport for a year.

## Resolution order

Work down this list and stop at the first signal that resolves it:

**1. Co-occurring tags.** The strongest signal, and usually sufficient. `"football"` next
to `sports.rugby` and `outdoors.running` is association football. Next to `usa`-flavoured
tags or `sports.baseball`, it is American football.

**2. The rest of the profile.** Job field, reading genres, other free text. `"bridge"`
from someone in `engineering` is a structure; from someone listing `games.cards` it is the
card game.

**3. Locale.** Weakest of the three and easy to over-trust. Use it to break a tie, never
as the primary signal — plenty of people live somewhere and follow the other sport.

**4. Nothing resolves it.** Return the ambiguity rather than guessing:

```json
{
  "tags": [],
  "confidence": 0.0,
  "ambiguous": true,
  "candidates": ["sports.soccer", "sports.american-football"],
  "retain_verbatim": "football"
}
```

An unresolved ambiguity is a much better outcome than a confident wrong answer. The
profile keeps the verbatim text, the cohort assignment falls back to other signals, and
the player can disambiguate in settings.

## Common ambiguities

| Term | Candidates | Usually resolved by |
|---|---|---|
| football | soccer / American / Australian rules | co-occurring sports tags, then locale |
| hockey | ice / field | locale, then co-occurring outdoors tags |
| chips | food.snacks / tech.hardware | job field |
| bridge | games.cards / tech.civil-engineering | job field |
| pool | games.billiards / outdoors.swimming | co-occurring games or fitness tags |
| dressing | food.cooking / medicine | job field |
| mining | geology / crypto / games.sandbox | co-occurring tech or science tags |
| model | fashion / science.modelling / hobbies.scale-models | job field, then reading genres |

## Do not ask

There is no interactive turn available at classification time — this runs in a batch
during onboarding. "Ask the user" is not an option here. Low confidence plus retained
verbatim text is the correct terminal state, and the settings UI is where the player gets
to correct it.

## Cost of each failure mode

Worth keeping in proportion:

- **Guessed wrong, high confidence** — the player gets themed puzzles about a thing they
  do not care about, and has no idea why. Worst outcome.
- **Returned ambiguous** — the player gets slightly less personalized puzzles until they
  edit their profile. Mild.

Bias toward the second.
