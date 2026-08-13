# 3. Personalization & Cohorts

## 3.1 The problem this solves

The requirement is "similar profiled users should receive similar if not the same themed
crosswords." Read literally that's a similarity constraint. Read practically it's the answer to
three problems that per-user generation can't solve:

- **Cost.** A crossword costs roughly $0.15–0.40 of model spend to generate well. Per user per
  tier per day, at 100k users, that's $45k–120k *daily*. Per cohort, at 400 cohorts, it's
  $180–480 daily. Three orders of magnitude, for a difference most players cannot perceive.
- **Quality control.** You can spot-check 1,200 puzzles a day. You cannot spot-check 300,000.
  Cohorts are the unit that makes human review of a meaningful sample possible.
- **Social texture.** Shared puzzles make comparative stats, cohort medians, and eventually
  discussion possible. A puzzle only you have ever seen is lonelier than it sounds.

So: **cohorts are the generation unit.** Personalization operates by putting you in the right
cohort, not by generating something unique.

## 3.2 Profile schema

```python
class PuzzleProfile(BaseModel):
    # Interests — normalized taxonomy tags
    interests: list[InterestTag] = Field(min_length=0, max_length=15)
    custom_interests: list[str] = Field(default_factory=list, max_length=5)

    # Work
    professional_field: ProfessionalField | None = None
    role_freetext: str | None = Field(default=None, max_length=80)
    years_experience: ExperienceBand | None = None

    # Reading & writing — the clue-voice signal
    reading_genres: list[ReadingGenre] = Field(default_factory=list, max_length=5)
    writing_practice: WritingPractice = WritingPractice.NONE
    wordplay_affinity: WordplayAffinity = WordplayAffinity.NEUTRAL

    # Solving
    baseline_difficulty: Literal[1, 2, 3] = 2
    preferred_tiers: list[Literal[1, 2, 3]] = Field(default_factory=lambda: [1, 2, 3])
    avoid_topics: list[InterestTag] = Field(default_factory=list, max_length=10)

    # Derived — not user-set
    cohort_id: UUID | None = None
    profile_version: int = 1
    embedding: list[float] | None = None   # 256-d, pgvector
```

### The interest taxonomy

A **curated, versioned tag set** of ~120 tags across 12 domains — not free-form text.

```
outdoors.hiking          outdoors.climbing        outdoors.camping
outdoors.cycling         outdoors.running         outdoors.surfing
music.classical          music.jazz               music.rock_indie
music.hiphop             music.electronic         music.musical_theatre
science.astronomy        science.biology          science.chemistry
science.physics          science.geology          science.medicine
food.baking              food.cooking             food.wine
food.coffee              food.bbq                 food.vegetarian
...
```

Curated tags rather than free text, for reasons that all point the same way:

- Cluster quality is dramatically better over a bounded vocabulary.
- Cohorts become **explainable** — "Outdoors + Science + Literary Fiction" is something a human
  reviewer, a support agent, and the player themself can understand. A 256-dimensional vector is
  not.
- Theme generation gets a stable, curatable input. We can build and reuse a theme-seed library
  keyed on tags.
- It bounds the prompt-injection surface on the highest-volume model input.

Free text is still accepted (`custom_interests`, `role_freetext`) and handled in two ways: mapped
onto existing tags by a cheap `claude-haiku-4-5` classification call at write time, and retained
verbatim as low-weight flavour input to theme ideation. Unmappable free text is queued for
taxonomy review — that queue is how the taxonomy grows, and it should be looked at monthly.

## 3.3 Cohorting

### Step 1 — Canonical profile string

Deterministic, sorted, stable. Determinism matters: an unstable string means unstable embeddings
means players drifting between cohorts for no reason.

```
interests: food.baking, literature.poetry, outdoors.hiking, science.biology
field: healthcare
reads: literary_fiction, poetry
writes: creatively
wordplay: loves_puns
difficulty: 2
```

### Step 2 — Hybrid representation

Not a pure embedding. A concatenation of:

- **Tag multi-hot vector** (120-d, weighted by domain diversity so a player with four tags in one
  domain doesn't dominate)
- **Field one-hot** (40-d)
- **Reading/writing/wordplay one-hot** (~20-d)
- **Semantic embedding of the free-text remainder** (96-d, reduced), covering only
  `custom_interests` and `role_freetext`

The structured part dominates deliberately. Cohorts should be *legible*; a purely semantic
clustering produces groupings nobody can name, which makes review and debugging painful. The
embedding column exists for the free-text tail and for the fast nearest-centroid check.

### Step 3 — Cluster

**HDBSCAN** over the hybrid vectors, with:

- `min_cluster_size = 150` — below this, per-cohort generation cost stops amortizing.
- `max_cluster_size ≈ 5,000` — above this, split; cohorts that large aren't personalized.
- Noise points (HDBSCAN's `-1` label) go to the nearest centroid by cosine distance, or to the
  **general-audience cohort** if nothing is within threshold.

Why HDBSCAN over k-means: we don't know k, cohort density genuinely varies (there are far more
generalist players than marine biologists), and HDBSCAN's explicit noise handling is exactly
right for the long tail of unusual profiles.

### Step 4 — Name and describe

For each cohort, one `claude-opus-5` call produces a human-readable name and a
**theme-generation brief** — a paragraph that becomes cached prompt context for every subsequent
theme ideation call for that cohort:

```json
{
  "cohort_id": "0193...",
  "name": "Outdoor Scientists",
  "size": 1240,
  "dominant_tags": ["outdoors.hiking", "science.biology", "outdoors.camping",
                    "science.geology", "literature.nonfiction"],
  "dominant_fields": ["research", "education", "healthcare"],
  "clue_voice": "Precise and definitional, comfortable with technical vocabulary.
                 Enjoys clever misdirection but not groan-puns. Reads long-form
                 non-fiction; expect familiarity with science journalism.",
  "theme_brief": "Themes drawing on field biology, geology, hiking and trail
                  culture, natural history, and the language of scientific
                  observation. Wordplay on species names, geological eras, and
                  outdoor gear lands well. Avoid themes requiring deep pop-culture
                  or sports knowledge."
}
```

This brief is a **cached prompt prefix** (see §3.6) and it is the artifact that makes cohort
personalization actually show up in the puzzles.

### Step 5 — Migration with hysteresis

A player only moves cohorts when:

- their new cluster assignment differs, **and**
- the new centroid is meaningfully closer (>15% relative improvement in cosine distance), **and**
- they haven't moved in the last 14 days

Without hysteresis, borderline profiles ping-pong weekly, which is confusing and destroys the
archive's coherence. With it, a player's puzzle history stays a mostly-continuous thing.

## 3.4 Cohort sizing

| Users | Cohorts | Avg size | Puzzles/day | Est. daily model spend |
|---|---|---|---|---|
| 1k | 8 | 125 | 24 | ~$5 |
| 10k | 45 | 220 | 135 | ~$27 |
| 100k | 280 | 360 | 840 | ~$170 |
| 1M | 800 | 1,250 | 2,400 | ~$480 |

Cohort count grows sublinearly — roughly with the *diversity* of the population rather than its
size — because interest space saturates. This is the property that makes the economics work: at
1M users the marginal player costs essentially nothing to serve.

(Spend figures use the per-puzzle cost model in
[Puzzle generation §4.8](04-puzzle-generation.md#48-cost-model). They assume batch pricing and
prompt caching, and they are the thing most worth re-measuring against reality in week one.)

## 3.5 Difficulty calibration within a cohort

Cohorts share themes and vocabulary. They do **not** share a difficulty curve — a cohort contains
new solvers and 20-year veterans. Two mechanisms handle this:

1. **Tier selection is the player's**, always. Anyone can open any tier.
2. **The default tier adapts** per player. A rolling window of solve times and assist rates,
   normalized against the cohort median for that puzzle:

```python
def suggest_tier(history: SolveHistory, current: int) -> int:
    recent = history.last_n(10)
    if recent.solve_rate > 0.85 and recent.median_ratio < 0.7 and recent.assist_rate < 0.15:
        return min(current + 1, 3)      # consistently fast and unassisted → nudge up
    if recent.solve_rate < 0.45 or recent.assist_rate > 0.55:
        return max(current - 1, 1)      # struggling → nudge down
    return current
```

`median_ratio` is the player's time over the cohort's median time for the same puzzle — which is
only possible *because* cohorts share puzzles. Adaptive difficulty falls out of the cohort design
for free.

The suggestion is surfaced, never imposed: *"You've been flying through Warmup — want to try
Daily?"*

## 3.6 Prompt caching and cohort briefs

The cohort's `theme_brief` and `clue_voice`, plus the shared construction style guide, form a
stable prompt prefix reused across every generation call for that cohort. It is marked with
`cache_control` so repeated calls read it at roughly a tenth of input cost.

```python
system = [
    {"type": "text", "text": CONSTRUCTION_STYLE_GUIDE},      # ~6k tokens, global
    {"type": "text", "text": cohort.theme_brief_expanded,    # ~1.5k tokens, per cohort
     "cache_control": {"type": "ephemeral", "ttl": "1h"}},
]
```

The ordering is the whole trick. Caching is a **prefix match**: the global style guide must come
first (stable across all cohorts, so its cache survives cohort changes), then the cohort brief,
then the volatile per-call content after the breakpoint. Interpolating a date or a cohort ID into
the style guide would invalidate everything after it and silently cost us the cache.

The nightly sweep processes a cohort's three tiers back to back specifically to keep these reads
inside the cache TTL.

## 3.7 Privacy

- Profile data drives puzzle generation and nothing else. It is not sold, not used for ad
  targeting, and not shared across accounts.
- Cohort membership is never exposed as a member list. Aggregate stats (median solve time) are
  reported only for cohorts above a minimum size, so they can't be used to infer an individual.
- Free-text inputs are stored, model-facing, and deletable. The privacy policy says so in those
  words.
- Profile deletion removes the profile row, the embedding, and cohort membership within 30 days.
  Already-generated cohort puzzles persist — they aren't personal data, they're a shared artifact.
- Data export returns the profile, solve history, and cohort assignments as JSON.
