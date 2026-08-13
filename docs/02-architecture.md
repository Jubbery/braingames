# 2. Architecture

## 2.1 Shape

Five deployable units. Deliberately few — this is a modular monolith with two workers, not a
microservice estate. The seams are drawn where the scaling and failure characteristics actually
differ.

```
                    ┌─────────────────────────────────┐
   Browser  ───────▶│  web  (Vite static build → CDN) │
   (PWA)            └─────────────────────────────────┘
       │
       │  HTTPS / JSON
       ▼
   ┌────────────────────────────────────────────────────┐
   │  api   FastAPI                                     │
   │  auth · profiles · puzzle serving · progress sync  │
   │  stats · archive · admin                           │
   └───────┬───────────────────────┬────────────────────┘
           │                       │
           ▼                       ▼
   ┌───────────────┐       ┌──────────────┐
   │  Postgres 17  │       │  Redis       │
   │  + pgvector   │       │  cache/locks │
   └───────▲───────┘       └──────▲───────┘
           │                      │
   ┌───────┴──────────────────────┴────────────────────┐
   │  worker-gen   arq                                  │
   │  nightly: cohorts → themes → grids → fills → clues │
   │           → QA → theme code → publish              │
   └───────┬─────────────────────────┬──────────────────┘
           │                         │
           ▼                         ▼
   ┌───────────────┐        ┌──────────────────┐
   │ Anthropic API │        │  R2 / S3         │
   │ (Batch + sync)│        │  theme bundles   │
   └───────────────┘        └──────────────────┘

   ┌────────────────────────────────────────────────────┐
   │  worker-cluster   arq (weekly)                     │
   │  re-embed profiles · re-cluster cohorts · migrate  │
   └────────────────────────────────────────────────────┘
```

### Why these five

| Unit | Scales with | Fails how |
|---|---|---|
| `web` | CDN, effectively free | Static; can't really |
| `api` | Concurrent solvers (spiky, timezone-driven) | User-visible immediately |
| `worker-gen` | Cohort count × 3 tiers, once daily | Invisible for hours; degrades tomorrow, not today |
| `worker-cluster` | Total user count, weekly | Invisible for a week |
| Datastores | Managed | Everything |

`api` and `worker-gen` have opposite profiles — one is latency-critical and bursty, the other is
throughput-oriented and batch. That's the whole argument for splitting them and the reason not to
split further.

## 2.2 The `api` service

FastAPI, one process per core behind Uvicorn, stateless, horizontally scalable.

```
api/
├── main.py                  app assembly, middleware, exception handlers
├── config.py                pydantic-settings; all config from env
├── deps.py                  DI: db session, redis, current_user
├── domain/                  pure logic, no I/O — the testable core
│   ├── puzzle.py            Puzzle/Grid/Entry/Clue models + invariants
│   ├── progress.py          merge rules for cross-device sync
│   ├── scoring.py           streaks, stats aggregation
│   └── profile.py           profile normalization, taxonomy mapping
├── routers/
│   ├── auth.py  profiles.py  games.py  crossword.py
│   ├── progress.py  stats.py  archive.py  admin.py
├── services/                orchestration; I/O lives here
│   ├── puzzle_service.py  profile_service.py  cohort_service.py
│   ├── progress_service.py  theme_service.py
├── repositories/            SQLAlchemy 2.0 async; the only place SQL lives
└── models/                  SQLAlchemy ORM
```

`domain/` has no database and no network. Grid invariants, progress merge, and streak math are
the parts most likely to be wrong in a subtle way, and they're the parts that should be testable
with a plain function call.

### Auth

- Email + password (Argon2id) and OAuth (Google, Apple).
- Access token: JWT, 15 min, in memory on the client.
- Refresh token: opaque, 30 days, rotating, `httpOnly` + `Secure` + `SameSite=Lax` cookie.
  Reuse detection revokes the family.
- Anonymous play is supported via a device-scoped anonymous account, upgradeable in place so a
  player never loses a streak by signing up late.

### Serving a puzzle

`GET /v1/games/crossword/daily?date=2026-08-13` is the hot path and must be boring:

1. Resolve player → `cohort_id` (cached in Redis, 1h TTL).
2. Look up `puzzles` by `(game=crossword, cohort_id, date, tier)`.
3. Return a signed CDN URL for the puzzle JSON and, separately, the theme bundle manifest.

Puzzle payloads are immutable and content-addressed, so they're cached aggressively at every
layer. The API returns metadata plus URLs, not the puzzle body — this keeps the API response
small and lets the CDN do the work.

**Cold-start fallback.** If no puzzle exists for a cohort/date/tier (new cohort, failed
generation, a clock edge), the API serves from the **evergreen pool** — a standing library of
several hundred general-audience puzzles per tier, generated in advance and human-spot-checked.
The player sees a good crossword. They never see an empty state. This one fallback removes most
of the operational anxiety from the nightly job.

## 2.3 The `worker-gen` service

`arq`, Redis-backed, running one DAG per cohort per night.

### The nightly run

Triggered hourly, sweeping timezones so each cohort's puzzles are ready ~6 hours before that
cohort's modal local midnight.

```
sweep(hour_utc)
  └─ for each cohort whose members are mostly in the timezones dropping soon:
       ├─ acquire Redis lock (cohort_id, date)        ← idempotency
       ├─ generate_day(cohort_id, date)
       │    ├─ theme ideation           (1 LLM call, Opus 5)
       │    ├─ for tier in 1..3:
       │    │    ├─ theme entry selection + validation
       │    │    ├─ grid template match
       │    │    ├─ fill  (solver, no LLM)
       │    │    ├─ clue writing         (batched LLM, Sonnet 5)
       │    │    ├─ automated QA         (rules + 1 LLM call, Opus 5)
       │    │    └─ repair loop, max 3 attempts
       │    ├─ visual theme code gen     (1 LLM call, Opus 5 — cached per theme)
       │    ├─ theme code validation + sandbox smoke test
       │    └─ publish (write puzzle JSON to R2, row to Postgres, bust cache)
       └─ release lock
```

Every stage is a separate `arq` job with its own retry policy, and every stage's output is
persisted. A clue-writing failure at 3am does not re-run theme ideation and grid filling; it
resumes from the stored fill. This matters because the pipeline is long and the expensive parts
are at the front.

### Batch vs. synchronous

Clue writing is the token-heavy stage and it is not latency-sensitive — puzzles are generated
hours ahead. It runs through the **Message Batches API** (50% of standard price, results within
an hour typically). Theme ideation and QA run synchronously because the DAG blocks on them.

Detail in [Puzzle generation](04-puzzle-generation.md).

## 2.4 The `worker-cluster` service

Weekly, plus on-demand when the changed-profile count crosses a threshold:

1. Rebuild the canonical profile string for every changed profile.
2. Embed it (see [Personalization & cohorts](03-personalization-and-cohorts.md) for why we use a
   deterministic hybrid rather than a pure embedding).
3. Re-cluster.
4. Diff old and new assignments; migrate members, with hysteresis so people don't oscillate.
5. Create new cohorts, retire empty ones.

Cohort membership changes are announced to the player, never silent, and never mid-day.

## 2.5 Data flow: profile edit → new puzzles

```
Player edits interests
   │
   ├─▶ PATCH /v1/profile              api validates + normalizes to taxonomy
   │       │
   │       ├─▶ profiles table updated, profile_version++
   │       ├─▶ fast cohort re-check: cosine vs. existing centroids
   │       │     ├─ still closest to current cohort → nothing changes
   │       │     └─ closer to another → reassign, effective tomorrow
   │       └─▶ response tells the player exactly what happens and when
   │
   └─▶ tomorrow's sweep generates for the new cohort as usual
```

The fast re-check against existing centroids is what makes preference edits feel responsive
without running the clustering job. Full re-clustering (which can *create* cohorts) stays weekly.

## 2.6 Environments and deployment

| Env | Purpose | Generation |
|---|---|---|
| `local` | Docker Compose: Postgres, Redis, MinIO, api, worker | On demand via CLI, real API, small budget cap |
| `preview` | Per-PR ephemeral | Fixture puzzles; no LLM calls |
| `staging` | Full pipeline, synthetic cohorts | Nightly, real API, reduced cohort count |
| `production` | | Nightly, full |

Deployment target: containers on Fly.io or Railway to start (both give managed Postgres and
Redis, and multi-region for `api`), with the escape hatch to ECS/GKE if scale demands it. `web`
is a static build on Cloudflare Pages; puzzle JSON and theme bundles sit in R2 behind the same
CDN.

### Observability

- **Structured JSON logs**, correlation ID per request and per generation run.
- **OpenTelemetry** traces. The generation DAG is one trace with a span per stage — this is the
  single most useful debugging artifact when a cohort's puzzles come out badly.
- **Metrics**: generation success rate per stage, fill solver time and backtrack count, QA
  rejection reasons histogram, LLM tokens and cost per cohort per day, puzzle serve latency,
  solve/abandon rates by tier and cohort.
- **Alerts**: any cohort without published puzzles 4h before its local midnight; QA rejection
  rate over 20%; daily LLM spend over budget; fallback-pool serve rate over 2%.

The last one deserves emphasis: the evergreen fallback means generation failures are *silent* to
players, which means they will also be silent to us unless we alert on the fallback rate
specifically.

## 2.7 Security notes

- Generated theme code is untrusted. It runs in a sandboxed `iframe` with a locked-down CSP and
  no access to the parent origin, cookies, or the network. Full treatment in
  [Theme engine](05-theme-engine.md).
- Puzzle answers are served with the puzzle. Client-side answer checking is the norm for this
  genre and the alternative (a network round-trip per keystroke) is a worse product. Leaderboards
  are therefore best-effort, not competitive-integrity-grade, and we should not pretend otherwise.
- Free-text profile input (job title, custom interests) is model-facing. It is length-capped,
  stripped of control characters, and inserted into prompts inside delimited blocks with an
  explicit instruction that its contents are data, not instructions. A player writing "ignore
  previous instructions" into their job title should get a puzzle about that phrase, not a
  compromised prompt.
- Profile data is personal. It is not shared between accounts, cohort membership is never exposed
  as a member list, and profile deletion is a real delete with a documented retention window.
