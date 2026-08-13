---
id: prompt-assembly-invariants
kind: pattern
applies_to:
  - codegen_backend
  - codegen_review
summary: >
  Use when touching prompt assembly, routing, or anything that feeds a model call. The
  caching invariants are silent when broken — nothing errors, the bill just goes up 10x.
triggers:
  always: true
version: 1
status: active
required: true
priority: 10
evidence: packages/kb/tests/test_assembler.py
review_by: 2027-02-01
owner: backend
---

# Prompt assembly invariants

Prompt caching is a **prefix match**. Any byte change anywhere in the prefix invalidates
everything after it. Break one of these rules and nothing errors — the cache hit rate
just drops to zero and input cost rises roughly tenfold.

That silence is the whole problem. These invariants exist because the failure mode
cannot be caught by ordinary testing.

## The block order

```
block 0  GLOBAL SPINE   identical for every cohort → one cache entry for the whole run
block 1  ROUTED DOCS    stable per (cohort, tier)
block 2  INDEX          stable per kb_version, carries the breakpoint
─────────── cache breakpoint ───────────
user turn               everything volatile lives here
```

Most stable first. Putting anything cohort-specific ahead of the spine gives every
cohort its own cache entry and multiplies cache-write cost by the cohort count.

## The rules

**1. Nothing volatile above the breakpoint.** No dates, no run ids, no cohort ids in the
spine, no `datetime.now()`. `assemble()` scans for these and raises
`VolatilePromptError` naming the offending block. Do not work around that error by
loosening the scanner — it is the only thing standing between you and a silent 10x.

**2. Ordering must be total and explicit.** Documents sort by `(priority, ref)`. Never
rely on set or dict iteration order: it will hold in testing, reorder in production after
an unrelated change, and destroy the cache with no failing test.

**3. Routing may not depend on runtime identifiers.** `RoutingContext` deliberately has
no run id and no timestamp. If you find yourself wanting to add one, that is a signal the
value belongs in the user turn.

**4. Do not change the tool set mid-conversation** without the mid-conversation tool
changes beta. Tools render at position 0; adding one invalidates everything.

## When you change any of this

- `test_assembly_is_byte_identical` and `test_assembly_is_stable_under_filesystem_order`
  must still pass. If you need to change what they assert, that is a design decision, not
  a test fix.
- Watch the cache-hit column in `bg costs` after deploying. A ratio trending toward zero
  is the canary; it is the fastest signal that something volatile crept in.

## Things that look like optimisations and are not

- **Semantic retrieval in the nightly pipeline.** It returns different sets for
  near-identical inputs, which destroys both cache survival and reproducibility. Routing
  keys are already structured; embeddings buy nothing here. Agentic retrieval via
  `read_knowledge` is the sanctioned path for open-ended stages.
- **Deduplicating repeated text across blocks.** The repetition costs a few tokens once
  per cache write; restructuring blocks to avoid it costs the cache.
- **Sorting documents by relevance.** Relevance ordering varies per call. Priority
  ordering does not.
