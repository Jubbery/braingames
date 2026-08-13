# 6. Data Model

Postgres 17 with `pgvector`. Everything is UUIDv7 keyed (time-sortable, index-friendly).
Timestamps are `timestamptz`, always UTC.

## 6.1 Identity and profile

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY,
    email           CITEXT UNIQUE,
    password_hash   TEXT,                            -- NULL for OAuth-only
    display_name    TEXT,
    timezone        TEXT NOT NULL DEFAULT 'UTC',     -- IANA; drives the daily drop
    is_anonymous    BOOLEAN NOT NULL DEFAULT FALSE,
    subscription_tier TEXT NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE TABLE puzzle_profiles (
    user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    interests           TEXT[]  NOT NULL DEFAULT '{}',   -- taxonomy tags
    custom_interests    TEXT[]  NOT NULL DEFAULT '{}',
    professional_field  TEXT,
    role_freetext       TEXT,
    reading_genres      TEXT[]  NOT NULL DEFAULT '{}',
    writing_practice    TEXT    NOT NULL DEFAULT 'none',
    wordplay_affinity   TEXT    NOT NULL DEFAULT 'neutral',
    baseline_difficulty SMALLINT NOT NULL DEFAULT 2,
    avoid_topics        TEXT[]  NOT NULL DEFAULT '{}',
    embedding           VECTOR(256),
    cohort_id           UUID REFERENCES cohorts(id),
    cohort_assigned_at  TIMESTAMPTZ,
    profile_version     INT     NOT NULL DEFAULT 1,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON puzzle_profiles USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON puzzle_profiles (cohort_id);
```

`cohort_assigned_at` exists to enforce the 14-day migration hysteresis from
[Personalization §3.3](03-personalization-and-cohorts.md).

## 6.2 Cohorts

```sql
CREATE TABLE cohorts (
    id                UUID PRIMARY KEY,
    name              TEXT NOT NULL,              -- "Outdoor Scientists"
    centroid          VECTOR(256) NOT NULL,
    member_count      INT NOT NULL DEFAULT 0,
    dominant_tags     TEXT[] NOT NULL,
    theme_brief       TEXT NOT NULL,              -- cached prompt context
    clue_voice        TEXT NOT NULL,
    is_general        BOOLEAN NOT NULL DEFAULT FALSE,  -- the catch-all cohort
    status            TEXT NOT NULL DEFAULT 'active',  -- active | retiring | retired
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_clustered_at TIMESTAMPTZ
);

CREATE INDEX ON cohorts USING hnsw (centroid vector_cosine_ops);
```

Exactly one cohort has `is_general = TRUE`. It is never retired and never empty; it's where
skipped profiles and clustering noise points land, and its puzzles double as the evergreen pool.

## 6.3 Puzzles

```sql
CREATE TABLE puzzles (
    id            UUID PRIMARY KEY,
    game_id       TEXT NOT NULL,                     -- 'crossword' | 'mini' | ...
    cohort_id     UUID REFERENCES cohorts(id),       -- NULL = evergreen/global
    puzzle_date   DATE NOT NULL,
    tier          SMALLINT,                          -- NULL for games without tiers
    theme_id      UUID REFERENCES themes(id),

    content_hash  TEXT NOT NULL,                     -- addresses the JSON blob
    content_url   TEXT NOT NULL,                     -- R2 object URL
    size          SMALLINT NOT NULL,                 -- 15, 21, 5
    word_count    SMALLINT,
    mean_fill_score REAL,

    status        TEXT NOT NULL DEFAULT 'draft',     -- draft|qa|published|rejected|archived
    is_evergreen  BOOLEAN NOT NULL DEFAULT FALSE,
    qa_report     JSONB,
    generation_meta JSONB,                           -- model ids, tokens, timings, template id

    published_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (game_id, cohort_id, puzzle_date, tier)
);

CREATE INDEX ON puzzles (game_id, cohort_id, puzzle_date DESC) WHERE status = 'published';
CREATE INDEX ON puzzles (status, created_at) WHERE status IN ('draft', 'qa');
```

The unique constraint is the idempotency guarantee for the nightly job: a re-run cannot produce
a duplicate, so the worker's Redis lock is an optimization rather than a correctness requirement.

### The puzzle JSON

Stored in R2, immutable, content-addressed. This is the format the client actually consumes.

```json
{
  "schemaVersion": 1,
  "id": "0193f2a1-...",
  "game": "crossword",
  "date": "2026-08-13",
  "tier": 2,
  "size": { "rows": 15, "cols": 15 },

  "theme": {
    "id": "0193f2a0-...",
    "title": "Taking the Scenic Route",
    "trick": "Familiar phrases reinterpreted as trail directions.",
    "explanation": "Each theme answer is a common expression that doubles as...",
    "bundleHash": "sha256-9f3c...",
    "visualMotif": "topographic contour lines in dusk palette"
  },

  "grid": {
    "pattern": "....#....#....#...#..........", 
    "cells": [
      { "r": 0, "c": 0, "number": 1, "answer": "SUMMIT", "isTheme": false },
      { "r": 0, "c": 1, "number": null, "answer": "U" }
    ]
  },

  "entries": [
    {
      "id": "a1",
      "number": 1,
      "direction": "across",
      "row": 0, "col": 0, "length": 6,
      "answer": "SUMMIT",
      "clue": "Peak achievement?",
      "clueType": "wordplay",
      "difficulty": 3,
      "isTheme": false,
      "markers": []
    }
  ],

  "rebus": [
    { "r": 7, "c": 3, "value": "TREE" }
  ],

  "meta": {
    "wordCount": 76,
    "themeSquares": 42,
    "estimatedMinutes": 11.5,
    "constructedBy": "braingames-pipeline/1.0"
  }
}
```

Design notes on the format:

- **Answers ship with the puzzle.** Client-side checking is required for a responsive solving
  experience, and every product in this genre does it. The consequence — leaderboards are
  best-effort, not tamper-proof — is accepted explicitly rather than half-mitigated.
- `entries` is the primary structure; `grid.cells` is derived and included for convenience. The
  client validates they agree on load and refuses a mismatched puzzle.
- `schemaVersion` is present from v1 so the client can refuse a format it doesn't understand
  rather than rendering it wrong.

## 6.4 Themes

```sql
CREATE TABLE themes (
    id             UUID PRIMARY KEY,
    title          TEXT NOT NULL,
    trick          TEXT NOT NULL,
    explanation    TEXT NOT NULL,
    visual_motif   TEXT NOT NULL,
    motif_key      TEXT NOT NULL,          -- normalized; enables cross-cohort bundle reuse

    tokens         JSONB NOT NULL,
    bundle_hash    TEXT,                   -- NULL until the visual theme is generated
    bundle_url     TEXT,
    scene_status   TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|css_only|tokens_only|failed
    validation_report JSONB,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON themes (motif_key);
```

`motif_key` is how "kelp forest at dusk" generated for the marine-biology cohort gets reused when
the general cohort lands on the same motif three weeks later. `scene_status` records exactly which
layer of the theme survived validation.

## 6.5 Progress and solving

The table that gets written to most, and the one whose merge semantics matter most.

```sql
CREATE TABLE puzzle_progress (
    id            UUID PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    puzzle_id     UUID NOT NULL REFERENCES puzzles(id),

    state         JSONB NOT NULL,       -- see below
    elapsed_ms    INT NOT NULL DEFAULT 0,
    is_complete   BOOLEAN NOT NULL DEFAULT FALSE,
    is_correct    BOOLEAN NOT NULL DEFAULT FALSE,
    used_check    BOOLEAN NOT NULL DEFAULT FALSE,
    used_reveal   BOOLEAN NOT NULL DEFAULT FALSE,
    autocheck_on  BOOLEAN NOT NULL DEFAULT FALSE,

    revision      BIGINT NOT NULL DEFAULT 1,     -- monotonic, server-assigned
    client_clock  JSONB NOT NULL DEFAULT '{}',   -- per-device Lamport counters
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (user_id, puzzle_id)
);

CREATE INDEX ON puzzle_progress (user_id, updated_at DESC);
```

`state` is compact because it's written often:

```json
{
  "letters": "SUMMIT..ORE.#....TRAIL#...",  
  "pencil":  "0000110000000000000000000",  
  "checked": [12, 13, 47],
  "revealed": [88],
  "cursor":  { "r": 3, "c": 7, "dir": "across" }
}
```

`letters` is a flat row-major string, `.` for empty and `#` for block. A 15×15 grid is 225
characters — small enough to sync on every idle tick without thinking about it.

### Sync merge

Devices sync opportunistically; conflicts are real. The merge rule is per-cell and
last-writer-wins on a Lamport clock, with two overrides:

1. **A correct letter beats an empty cell**, always, regardless of clock. Losing correct progress
   is the one outcome players actually notice and resent.
2. **`is_complete` is monotonic.** Once true, never false. `elapsed_ms` takes the *minimum* of
   the completing device's value, since that's the honest solve time.

The merge is a pure function in `domain/progress.py` and it has property tests. It's small,
it's subtle, and it's the code most likely to be wrong.

## 6.6 Stats and streaks

```sql
CREATE TABLE user_stats (
    user_id          UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    game_id          TEXT NOT NULL,
    current_streak   INT NOT NULL DEFAULT 0,
    longest_streak   INT NOT NULL DEFAULT 0,
    last_solve_date  DATE,                        -- in the user's local timezone
    total_solves     INT NOT NULL DEFAULT 0,
    solves_by_tier   JSONB NOT NULL DEFAULT '{}',
    assisted_solves  INT NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, game_id)
);

-- Per-puzzle aggregates, refreshed on a schedule. Powers "cohort median" and
-- the adaptive-tier suggestion.
CREATE MATERIALIZED VIEW puzzle_aggregates AS
SELECT
    p.id AS puzzle_id, p.cohort_id, p.tier,
    count(*) FILTER (WHERE pp.is_correct)               AS solves,
    count(*)                                            AS attempts,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY pp.elapsed_ms)
        FILTER (WHERE pp.is_correct AND NOT pp.used_reveal) AS median_ms,
    avg(pp.used_check::int + pp.used_reveal::int)       AS assist_rate
FROM puzzles p
JOIN puzzle_progress pp ON pp.puzzle_id = p.id
GROUP BY p.id, p.cohort_id, p.tier;
```

Streaks are computed in the user's timezone, not UTC. A player in Auckland and a player in Los
Angeles have different days, and getting this wrong breaks streaks at midnight for half the user
base — a bug that generates furious support tickets and is invisible in testing from one office.

## 6.7 Lexicon and templates

```sql
CREATE TABLE lexicon (
    word       TEXT PRIMARY KEY,             -- A-Z uppercase, no spaces
    display    TEXT NOT NULL,                -- "SLIPPERY SLOPE"
    score      SMALLINT NOT NULL,            -- 0..100
    obscurity  REAL NOT NULL DEFAULT 0.5,    -- 0..1
    tags       TEXT[] NOT NULL DEFAULT '{}',
    length     SMALLINT GENERATED ALWAYS AS (char_length(word)) STORED,
    source     TEXT NOT NULL
);
CREATE INDEX ON lexicon (length, score DESC);
CREATE INDEX ON lexicon USING gin (tags);

CREATE TABLE grid_templates (
    id               TEXT PRIMARY KEY,
    size             SMALLINT NOT NULL,
    pattern          TEXT NOT NULL,
    word_count       SMALLINT NOT NULL,
    theme_slots      JSONB NOT NULL,
    max_slot_length  SMALLINT NOT NULL,
    openness         REAL NOT NULL,
    difficulty_band  SMALLINT NOT NULL,
    fill_attempts    INT NOT NULL DEFAULT 0,   -- learned prior
    fill_successes   INT NOT NULL DEFAULT 0,
    avg_fill_ms      INT
);
```

The solver loads the lexicon into memory once at worker start as bitset indices; the table is the
durable source of truth and the editing surface, not the hot path.

## 6.8 Retention

| Data | Retention |
|---|---|
| Puzzles | Indefinite — they're the archive |
| Progress | Indefinite while the account lives |
| Generation traces (`generation_meta`, prompts) | 90 days |
| Deleted account | Profile, embedding, progress purged within 30 days |
| Anonymous accounts | Purged after 180 days of inactivity |
