---
id: freetext-normalization
kind: input_handling
applies_to:
  - profile_classification
summary: >
  Use when mapping a player's free-text interest or job title onto the interest taxonomy.
  Covers confident mapping, partial mapping, and what to do with genuinely unmappable input.
triggers:
  always: true
version: 1
status: active
priority: 30
owner: personalization
---

# Normalizing free-text interests

Players pick from a curated taxonomy, but they can also type. That free text is the most
useful signal we get about the long tail and the most likely to break things.

## The task

Given free text, return taxonomy tags with a confidence, plus a flag when nothing fits.

```json
{
  "tags": ["outdoors.misc", "sports.niche"],
  "confidence": 0.55,
  "unmappable": false,
  "retain_verbatim": "competitive cheese rolling"
}
```

## Rules

**1. Map to the most specific tag that is actually right.** `"trail running"` maps to
`outdoors.running` — not `outdoors.hiking`, which is a different activity, and not the
parent domain, which throws away signal.

**2. Multiple tags are fine and often correct.** `"I restore vintage motorcycles"` maps to
`tech.mechanical` and `history.material-culture`. Two decent tags beat one forced one.

**3. Confidence is about the *mapping*, not the interest.** `"reading"` is a real interest
and a terrible tag — it is too broad to distinguish cohorts. Low confidence, and lean on
the structured `reading_genres` field instead.

**4. Always retain the verbatim text.** Even a confident mapping loses detail.
`"competitive cheese rolling"` mapped to `outdoors.misc` is correct for clustering *and*
useless for theme ideation, where the original phrase is the whole point. The verbatim
string is passed to theme ideation as low-weight flavour input.

**5. Unmappable is a valid, useful answer.** If nothing in the taxonomy is within reach,
set `unmappable: true` and let it queue. Forcing a bad tag corrupts the cohort; queuing it
grows the taxonomy. The queue is read monthly and is the main mechanism by which the
taxonomy improves.

**6. Job titles are interests too, but weaker ones.** A veterinary nurse is comfortable
with clinical vocabulary, which is worth knowing. But people do not necessarily want
puzzles about their job. Map job titles to `field` tags, weighted lower than chosen
interests, and never make a job the theme unless the player also listed it as an interest.

## Worked examples

| Input | Tags | Confidence | Note |
|---|---|---|---|
| `bouldering` | `outdoors.climbing` | 0.95 | Clean specific match |
| `I play bass in a covers band` | `music.rock_indie`, `music.performance` | 0.80 | Two tags, both real |
| `football` | — | — | Ambiguous; see `ambiguity-resolution` |
| `competitive cheese rolling` | `outdoors.misc` | 0.45 | Queue it; retain verbatim |
| `stuff` | — | 0.0 | `unmappable: true`, nothing to retain |
| `RN, cardiac ICU` | field `healthcare` | 0.90 | Field, not interest |

## What not to do

- Do not invent taxonomy tags. The tag set is versioned and closed; proposing new ones is
  what the queue is for.
- Do not map to a tag because it is nearby in spelling. `"curling"` is not
  `outdoors.climbing`.
- Do not silently drop input you cannot map. Silent drops are invisible failures, and they
  are how a taxonomy stops growing.
