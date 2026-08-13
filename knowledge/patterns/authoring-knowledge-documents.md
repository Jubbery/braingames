---
id: authoring-knowledge-documents
kind: pattern
applies_to:
  - codegen_backend
  - codegen_review
summary: >
  Use when adding or editing a knowledge/ document. Covers frontmatter, writing the
  summary line for a model rather than a human, and the admission bar a document must clear.
triggers:
  always: true
version: 1
status: active
priority: 30
evidence: packages/kb/tests/test_loader.py
review_by: 2027-02-01
owner: backend
---

# Authoring a knowledge document

Start with `bg kb new`. It puts the file in the right directory, fills the frontmatter,
and sets `status: draft` — which is where every document starts, because promotion goes
through `bg kb eval`, not through the author's confidence.

## The summary line is the most important field

It becomes the index entry, and the index is all the model sees when deciding whether to
read the document. It is written for the model, not for a human browsing folders.

Lead with when to use it:

> Use when the motif involves drifting or falling particulate (snow, dust, embers,
> bubbles). Gives a frame-budget-safe pooled particle implementation with the density
> math.

Not:

> Particle field patterns and best practices.

The first tells a model whether this is the document it needs. The second describes a
filing category.

## Triggers decide whether it is ever seen

A non-core document with no trigger fails validation, because it can never be routed in.
Pick the narrowest trigger that is still correct:

- `always: true` — genuinely every call for that stage. Rare outside `input-handling/`.
- `tags` — cohort interest tags.
- `motif_keywords` — matched against motif *words*, not substrings, so `snow` does not
  fire on `snowboarding`.
- `tiers` — difficulty-specific guidance.

Over-broad triggers are the quiet failure. The document gets loaded on calls it does not
help, spends tokens, and dilutes attention across everything else in the prompt.

## Write the failure, not the feature

Documents that change behaviour lead with what goes wrong without them:

> Particles are the most requested theme effect and the most common way a scene gets
> unmounted for blowing its frame budget. The failure is almost always the same three
> mistakes.

A document that only describes the happy path reads as background and gets skimmed.

## Length

Every token is paid on every call the document routes into. The spine has a hard 8k cap
per stage; routed documents share a per-stage budget. If a document is growing past
roughly 1,500 tokens, it is probably two documents with different triggers.

## `required` is for safety, not importance

Most documents can be dropped to fit a budget — losing a pattern costs some polish. Set
`required: true` only where dropping it would be a correctness or safety failure, as with
`injection-defense`. A budget too small to hold its required documents raises rather than
silently omitting one, so the flag is a real constraint on budget tuning, not a hint.

## Getting to `status: active`

```
bg kb eval --doc <id> --against <golden-set>
```

A gate must improve and none may regress. The default verdict is reject: a document that
improves nothing is not neutral, it costs tokens and dilutes attention. Record the eval
in `evidence` so the next reviewer knows what justified it.

## Then publish

```
bg kb sync-skills
```

`.claude/skills/` is generated from `knowledge/`. CI fails if it is stale, so a knowledge
change that forgets this step is caught rather than leaving coding agents on old guidance.
Never edit files under `.claude/skills/` — they are output.
