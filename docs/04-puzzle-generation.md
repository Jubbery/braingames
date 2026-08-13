# 4. Puzzle Generation

## 4.1 The core design decision

The naive approach — "ask a model for a crossword" — fails, and it fails in a specific,
instructive way. Language models are genuinely excellent at the two creative acts in crossword
construction (having a theme idea, writing a clue) and genuinely unreliable at the one
combinatorial act (producing a legal, cleanly-filled, symmetric grid). Ask for the whole thing
and you get plausible-looking grids with asymmetric black squares, unchecked letters, entries
that don't cross, and a fill full of words nobody has heard of.

So the pipeline splits the work along that line:

| Stage | Who does it | Why |
|---|---|---|
| Theme ideation | `claude-opus-5` | Creative, open-ended, benefits from cohort context |
| Theme entry validation | Code | Length and symmetry are arithmetic |
| Grid template selection | Code | Search over a vetted library |
| Grid fill | Code (CSP solver) | Combinatorial, must be correct, must be fast |
| Clue writing | `claude-sonnet-5`, batched | Creative, high volume, per-entry |
| QA — mechanical | Code | Rules are rules |
| QA — editorial | `claude-opus-5` | Judgement about fairness and voice |
| Visual theme code | `claude-opus-5` | Creative; cached per theme |

Every LLM call is schema-constrained. Nothing in this pipeline parses free-form model prose.

## 4.2 Stage 1 — Theme ideation

**One call per cohort per day**, producing a theme concept plus candidate entry sets for all
three tiers.

The model gets the cohort brief (cached), the last 60 days of that cohort's themes (so it doesn't
repeat itself), and the day's date for seasonal awareness. It returns a structured object:

```python
class ThemeEntry(BaseModel):
    answer: str = Field(pattern=r"^[A-Z]{3,21}$")
    display: str            # "SLIPPERY SLOPE" — for the explanation, not the grid
    theme_role: str         # how this entry embodies the trick

class TierTheme(BaseModel):
    tier: Literal[1, 2, 3]
    is_themeless: bool
    entries: list[ThemeEntry] = Field(max_length=6)
    revealer: ThemeEntry | None = None
    seed_entries: list[str] = Field(default_factory=list)  # themeless: long fill to prefer

class ThemeConcept(BaseModel):
    title: str
    trick: str              # one sentence: what the wordplay actually is
    explanation: str        # the post-solve "about this theme" note
    visual_motif: str       # feeds the theme engine — see doc 05
    tiers: list[TierTheme] = Field(min_length=3, max_length=3)
```

Called with structured outputs so the shape is guaranteed:

```python
response = client.messages.create(
    model="claude-opus-5",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={
        "effort": "high",
        "format": {"type": "json_schema", "schema": ThemeConcept.model_json_schema()},
    },
    system=[
        {"type": "text", "text": CONSTRUCTION_STYLE_GUIDE},
        {"type": "text", "text": cohort.theme_brief_expanded,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ],
    messages=[{"role": "user", "content": theme_request(cohort, date, recent_themes)}],
)
```

Notes on the call shape, since these are the details that go stale:

- `thinking: {"type": "adaptive"}` — the model decides how much to think. Do not reach for
  `budget_tokens`; it is removed on Opus 5 and returns a 400.
- `effort` lives inside `output_config`, not at the top level. `high` is the right default here;
  the ideation call is the one place in the pipeline where quality dominates cost.
- No `temperature`. Sampling parameters are rejected on Opus 5. Variety comes from the
  recent-themes exclusion list and from prompting, not from a sampling knob.
- Thinking is **on by default** on Opus 5, and `max_tokens` caps thinking *plus* output. The
  16000 above is sized for both.

Theme variety across days is enforced by passing the last 60 days of titles and tricks with an
explicit instruction not to reuse a *mechanism* (not merely a topic) — "hidden words" twice in a
week is more repetitive than two different tricks about birds.

## 4.3 Stage 2 — Theme validation

Pure code, and it rejects a lot. That's the point: rejecting here is cheap, rejecting after the
fill is not.

```python
def validate_theme_set(entries: list[ThemeEntry], tier: int) -> ValidationResult:
    # 1. Every entry is A-Z only, 3..21 chars — enforced by the schema, re-checked here
    # 2. Lengths must be placeable symmetrically:
    #      - pairs of equal length, OR
    #      - odd-length entry centred on row 8 (15x15)
    # 3. No entry longer than 15
    # 4. Total theme squares within tier budget
    #      tier 1: 36-52   tier 2: 30-48   tier 3: 0 (themeless)
    # 5. No duplicate entries; no entry substring of another
    # 6. Every entry present in the lexicon or flagged for review
```

Failures return a structured critique to the ideation stage, which retries once with the specific
problem stated ("SLIPPERYSLOPE is 13 and NARROWPATH is 10 — they can't be a symmetric pair; give
me a set whose lengths pair up"). One retry, then fall through to a themeless Tier 2 or the
evergreen pool.

## 4.4 Stage 3 — Grid template selection

We maintain a library of **~2,000 hand-vetted 15×15 grid templates** (plus ~200 for 5×5 Mini),
each stored as a black-square pattern with precomputed metadata:

```python
class GridTemplate(BaseModel):
    id: str
    size: int
    pattern: str                        # 225 chars of '.' and '#'
    word_count: int
    theme_slots: list[SlotSpec]         # long Across slots usable for theme entries
    max_slot_length: int
    openness: float                     # avg slot length — proxy for fill difficulty
    difficulty_band: Literal[1, 2, 3]
```

Every template is validated once, at ingest, against the full legality checklist: 180° rotational
symmetry, all squares checked in both directions, no entries under 3 letters, fully connected
white space, word count in band. A template that passes is legal forever, which is why buying
this property once at ingest is so much better than trying to establish it per-puzzle.

Selection is a filter and a sort, not a search:

```python
def select_template(theme: TierTheme, tier: int) -> GridTemplate:
    lengths = sorted(e.answer for e in theme.entries)
    candidates = [
        t for t in TEMPLATES
        if t.difficulty_band == tier
        and can_place_symmetrically(t.theme_slots, lengths)
        and t.word_count <= WORD_LIMITS[tier]
    ]
    return max(candidates, key=lambda t: fill_success_prior(t, theme))
```

`fill_success_prior` is a learned scalar — we record fill success rate and solver time per
template and prefer templates that have historically filled well around similar theme-entry
shapes. This is the cheapest available lever on end-to-end generation latency.

Templates come from three sources: programmatic generation of symmetric patterns followed by
legality filtering, published open grid patterns, and constructor-contributed patterns. Grid
*patterns* are not copyrightable in the way a filled puzzle is, but we source deliberately and
document provenance anyway.

## 4.5 Stage 4 — Fill

A backtracking CSP solver with arc consistency. No model involvement, and it needs to run in
seconds, not minutes.

**Lexicon.** ~250k entries, each scored 0–100 for quality. Open sources: the Peter Broda Wordlist
and Spread the Wordlist (both freely licensed and community-maintained), plus a curated
cohort-vocabulary layer — domain terms that are fair game for that cohort and scored higher for
them than they would be generally.

```python
class LexiconEntry:
    word: str
    score: int          # 100 = great fill, 50 = acceptable, <30 = crosswordese, avoid
    tags: set[str]      # domain tags for cohort weighting
    obscurity: float    # 0..1, drives the crossing-fairness check
```

**Algorithm.**

```
1. Place theme entries in their slots. Fixed. Non-negotiable.
2. Build the constraint graph: slots as variables, letter positions as shared constraints.
3. Loop:
     a. Pick the unfilled slot with fewest remaining candidates (MRV heuristic),
        tie-broken by most crossings (degree heuristic).
     b. Order candidates by:
            score
          + cohort_tag_bonus(word, cohort)
          - obscurity_penalty(word, tier)
          + crossing_flexibility(word, slot)     # keeps future options open
     c. Try the top K (K≈40). For each:
          - propagate constraints to crossing slots
          - if any slot's candidate set empties → prune, next candidate
          - else recurse
     d. All K fail → backtrack.
4. On total failure: try the next template. After 3 templates, fail the tier.
```

**Performance.** Candidate sets are 256-bit bitsets over letter-position indices, so propagation
is bitwise AND. A precomputed index maps (length, position, letter) → bitset. Typical fill:
under 2 seconds. Hard themeless with stacked 15s: up to 30 seconds. The solver is `numpy`-backed
and runs in a thread pool so it doesn't block the worker's event loop.

**Quality gates on the completed fill:**

- Mean word score ≥ 55 (Tier 1), ≥ 50 (Tier 2), ≥ 45 (Tier 3)
- No entry scoring below 25
- At most 3 entries scoring below 40
- No two entries with `obscurity > 0.7` crossing each other — this is the single most important
  fairness rule and it is enforced mechanically, not by judgement

Failing a gate re-runs the fill with a raised score floor. Twice. Then the next template.

## 4.6 Stage 5 — Clue writing

The token-heavy stage: ~76 entries per puzzle × 3 tiers × N cohorts. It runs through the
**Message Batches API** at 50% of standard pricing, on `claude-sonnet-5` — cluing is
high-volume, well-specified work where Sonnet 5 is close to Opus quality at a materially lower
price. Theme entries and any entry the QA stage later rejects get re-clued on `claude-opus-5`.

Entries are batched ~20 per request so the model can see them together and avoid cross-clue
repetition, with the full grid supplied as context.

```python
class Clue(BaseModel):
    entry_id: str
    answer: str
    clue: str = Field(max_length=140)
    clue_type: Literal["definition", "wordplay", "fill_in_blank",
                       "cross_reference", "trivia", "pun"]
    difficulty: Literal[1, 2, 3, 4, 5]
    is_theme: bool
    markers: list[Literal["abbr", "foreign", "informal", "var", "prefix", "suffix"]]

class ClueBatch(BaseModel):
    clues: list[Clue]
```

```python
requests = [
    Request(
        custom_id=f"{puzzle_id}:{batch_idx}",
        params=MessageCreateParamsNonStreaming(
            model="claude-sonnet-5",
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": ClueBatch.model_json_schema()},
            },
            system=[
                {"type": "text", "text": CLUE_STYLE_GUIDE},
                {"type": "text", "text": cohort.clue_voice_expanded,
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": clue_request(grid, entries, tier)}],
        ),
    )
    for batch_idx, entries in enumerate(chunk(puzzle.entries, 20))
]

batch = client.messages.batches.create(requests=requests)
# poll batches.retrieve(batch.id) until processing_status == "ended",
# then stream batches.results(batch.id) — results arrive in ANY order,
# so key by custom_id, never by position.
```

The `custom_id` keying is not a stylistic preference. Batch results are explicitly unordered, and
assembling a puzzle from positionally-matched clues would produce a puzzle where every clue is
attached to the wrong answer — a bug that looks like a model failure and isn't.

The clue style guide encodes the NYT conventions the QA stage then enforces: clue and answer
agree in part of speech, tense, and number; abbreviated answers get an abbreviation signal
(`Abbr.`, or an abbreviation in the clue itself); foreign-language answers name the language;
`?` marks wordplay; fill-in-the-blanks are used sparingly; no answer word appears in its own clue.

Difficulty is steered per tier by ratio targets — Tier 1 is ~70% straight definitional cluing,
Tier 3 is ~60% wordplay and misdirection — and by explicitly permitting cohort-domain knowledge
at higher tiers.

## 4.7 Stage 6 — QA

Two passes. The mechanical one is not optional and never gets skipped for a "good" cohort.

### Mechanical (code, fast, blocking)

```python
CHECKS = [
    check_symmetry,                  # 180° rotational
    check_all_squares_checked,       # every square in both an Across and a Down
    check_min_word_length,           # ≥ 3
    check_connectivity,              # white space is one connected region
    check_word_count,                # within tier band
    check_no_duplicate_entries,
    check_no_answer_word_in_clue,    # stems too: RUNNING in a clue for RUN
    check_theme_symmetry,            # theme entries symmetrically placed
    check_clue_answer_agreement,     # POS/tense/number via spaCy
    check_abbreviation_markers,      # answer is abbr ⇒ clue signals it
    check_foreign_markers,
    check_crossing_fairness,         # no obscure × obscure
    check_profanity_and_sensitivity, # blocklist + category classifier
    check_clue_length,
    check_rebus_consistency,
]
```

Any failure produces a structured defect list. Defects are routed: mechanical grid failures go
back to the fill stage, clue failures go back to clue writing for those specific entries only.

### Editorial (one `claude-opus-5` call, advisory + blocking on severity)

The model is given the complete puzzle — grid, entries, clues, theme, tier, cohort brief — and
asked to review it as an editor:

```python
class Defect(BaseModel):
    severity: Literal["blocker", "major", "minor"]
    location: str                    # "12-Across" or "theme" or "grid"
    issue: str
    suggested_fix: str | None

class EditorialReview(BaseModel):
    theme_coherent: bool
    theme_discoverable: bool         # can a solver work out the trick?
    difficulty_matches_tier: bool
    estimated_solve_minutes: float
    defects: list[Defect]
    verdict: Literal["publish", "repair", "reject"]
```

One important prompt note: the review is asked for **coverage, not filtering** — report
everything including uncertain and low-severity findings, with severity attached, and let the
routing code decide. Telling a current model to "only report serious issues" makes it faithfully
suppress real findings it judges below the bar, and measured recall drops even though the model
found the bugs. Filter downstream, not in the prompt.

`repair` triggers targeted regeneration of the flagged items (max 3 rounds). `reject` fails the
tier to the evergreen pool and files an alert.

### Human review

A daily sample — 5% of puzzles plus every puzzle for a newly-created cohort plus everything the
editorial pass marked `repair` — goes to a review queue in the admin UI. Reviewer verdicts feed
back into the style guides and the template success priors. This is small, cheap, and the only
thing that keeps quality from drifting.

## 4.8 Cost model

Per 15×15 puzzle, with prompt caching on the style guides and cohort briefs:

| Stage | Model | Input | Output | Pricing | Cost |
|---|---|---|---|---|---|
| Theme ideation (÷3 tiers) | `claude-opus-5` | ~9k (7k cached) | ~4k | $5 / $25 per MTok | ~$0.038 |
| Clue writing | `claude-sonnet-5` | ~12k (8k cached) | ~6k | $3 / $15, batch −50% | ~$0.053 |
| Editorial QA | `claude-opus-5` | ~10k (6k cached) | ~2k | $5 / $25 | ~$0.066 |
| Repair rounds (amortized) | mixed | | | | ~$0.020 |
| **Total** | | | | | **~$0.18** |

Cached input reads at roughly 0.1× and cache writes at 1.25×, which is why the style guides sit
in the cached prefix and the volatile per-puzzle content sits after the breakpoint. Batch pricing
halves the clue stage, the largest token consumer.

Visual theme code generation is ~$0.10 per *theme*, not per puzzle, and is cached and reused
across cohorts with the same motif — call it $0.02 amortized.

At 400 cohorts × 3 tiers = 1,200 puzzles/day: **~$240/day, ~$7,200/month.** For 100k+ users
that's under a cent per user per month on generation.

The levers, in order of size, if that needs to come down: raise `min_cluster_size` (fewer
cohorts), drop the editorial QA call to `claude-sonnet-5` for cohorts with a good track record,
lower `effort` on ideation to `medium`, and generate Tier 3 every other day with the off-day
served from a themeless pool.

## 4.9 Failure handling

| Failure | Response |
|---|---|
| Theme ideation returns invalid entries | One retry with a structured critique, then themeless |
| No grid template fits | Relax word-count band, then drop the smallest theme entry |
| Fill fails on all 3 templates | Fall back to themeless fill for that tier |
| Batch clue job doesn't complete in time | Serve evergreen for that tier; publish when ready |
| Editorial verdict `reject` | Evergreen fallback + alert; puzzle retained for review |
| Anthropic API 429 / 529 | SDK-level retry with backoff; then defer to the next sweep |
| Whole cohort fails | Evergreen fallback for all tiers + page on-call |

The invariant behind all of it: **a player always gets a good crossword.** Personalization is an
enhancement layered on a reliable base, never a single point of failure between the player and a
playable puzzle.

## 4.10 The Mini, and other sizes

Mini (5×5) uses the same pipeline with the theme stage skipped and a single clue-writing call
on `claude-haiku-4-5`. Cost is roughly $0.004 per Mini. Sunday-size (21×21) uses the same
pipeline with a larger template library and 5–6 theme entries; it costs about 2.5× a 15×15 and
is a natural weekly premium feature.
