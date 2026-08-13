---
id: sensitive-topics
kind: input_handling
applies_to:
  - profile_classification
  - theme_ideation
  - editorial_qa
summary: >
  Use when a profile interest or candidate theme touches difficult subject matter. Draws
  the line between vocabulary that may appear in fill and subjects a theme may be about.
triggers:
  always: true
version: 1
status: active
required: true
priority: 30
owner: editorial
---

# Sensitive topics

The governing distinction: **a theme is a celebration, fill vocabulary is not.**

A puzzle whose theme is built on a subject is inviting the solver to enjoy it. A word
appearing once in the fill is just part of the language. These get different rules.

## Themes

Do not build a theme on:

- Active conflict, atrocity, or disaster.
- Illness, injury, or death as subject matter — including a specific diagnosis, even one
  the player listed. Someone who lists `science.medicine` wants medical *vocabulary*, not a
  theme about being ill.
- Addiction, self-harm, or abuse.
- Content sexualising anyone, and any sexual content at all.
- A living private individual.
- Current partisan politics. Historical politics is fine; this week's is not.

Adjacent themes are usually available and usually better. A player interested in military
history gets a theme about naval terminology entering everyday speech, not a theme about a
battle's casualties.

## Fill

Fill vocabulary is held to a much lower bar, because the alternative is an impoverished
lexicon. `WAR`, `DEATH`, `GRIEF` are ordinary English words and may appear.

What is excluded from fill entirely:

- Slurs and their variants, in any language.
- Explicit sexual terms.
- Words whose only common usage is as an insult toward a group.

The lexicon carries this as scoring rather than as a judgement call at generation time —
excluded terms score 0 and the solver never sees them.

## Clues

A clue can make a neutral answer inappropriate. `GAS` clued as a chemistry term is fine;
`GAS` clued with a reference to a historical atrocity is not. Clue the ordinary sense.

## When the player asked for it

`avoid_topics` always wins over `interests`. If a player lists `history` and avoids `war`,
generate history themes that route around it — trade routes, cartography, the history of
food — rather than dropping the interest.

The reverse does not hold. A player listing a sensitive interest does not unlock a theme
about it. The rules above are editorial standards, not preferences, and a player cannot
opt into a theme we would not otherwise make.

## When in doubt

Choose the adjacent theme. There is always another angle on an interest, and the cost of a
slightly less pointed theme is far lower than the cost of a puzzle that lands badly on
someone's difficult morning.
