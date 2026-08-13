# 7. API

REST over JSON. Versioned at `/v1`. OpenAPI 3.1 emitted by FastAPI; a typed TypeScript client is
generated in CI with `openapi-typescript` + `openapi-fetch`, so the frontend gets compile-time
safety without a shared-language monorepo.

## 7.1 Conventions

- `Authorization: Bearer <access_token>` on everything except auth and health.
- Errors are RFC 9457 problem details.
- Cursor pagination: `?cursor=<opaque>&limit=<n>`, response carries `next_cursor`.
- All timestamps ISO 8601 UTC. All dates are **local dates in the user's timezone** — the
  server resolves the timezone from the user record, so the client never computes "today".
- Idempotency: mutating endpoints accept `Idempotency-Key`.

```json
{
  "type": "https://braingames.app/errors/puzzle-not-found",
  "title": "Puzzle not found",
  "status": 404,
  "detail": "No published crossword for cohort 0193f2a1 on 2026-08-13 at tier 3.",
  "instance": "/v1/games/crossword/daily"
}
```

## 7.2 Auth

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/auth/register` | email + password → tokens |
| `POST` | `/v1/auth/login` | → access token + refresh cookie |
| `POST` | `/v1/auth/refresh` | rotating refresh; reuse revokes the family |
| `POST` | `/v1/auth/logout` | revokes the refresh family |
| `POST` | `/v1/auth/oauth/{provider}/callback` | Google, Apple |
| `POST` | `/v1/auth/anonymous` | device-scoped anonymous account |
| `POST` | `/v1/auth/upgrade` | anonymous → registered, **in place** — keeps streak and history |

`/v1/auth/upgrade` preserving identity is a product requirement, not an implementation detail. A
player who solves anonymously for two weeks and then signs up must not lose their streak.

## 7.3 Profile

```http
GET   /v1/profile
PATCH /v1/profile
GET   /v1/profile/taxonomy          # the interest tag tree; cacheable, versioned
GET   /v1/profile/cohort            # name + description of the player's cohort
```

`PATCH /v1/profile` response is designed to let the UI tell the truth about timing:

```json
{
  "profile": { "...": "..." },
  "cohort": {
    "changed": true,
    "from": { "id": "0193...", "name": "General" },
    "to":   { "id": "0194...", "name": "Outdoor Scientists" },
    "effective": "2026-08-14",
    "message": "You'll start getting puzzles from Outdoor Scientists tomorrow.",
    "available_today": [
      { "tier": 1, "puzzle_id": "0195..." },
      { "tier": 2, "puzzle_id": "0196..." }
    ]
  }
}
```

`available_today` is the nice moment described in [Product spec §1.3](01-product-spec.md#13-preferences-are-editable-always):
if the new cohort's puzzles already exist, offer them immediately.

## 7.4 Games

```http
GET /v1/games
```

Returns the game catalog with per-game today-state. This is the home screen's single request, and
its shape is what makes adding Connections a config change rather than a client rewrite:

```json
{
  "games": [
    {
      "id": "crossword",
      "name": "Crossword",
      "tagline": "Your daily personalized grid",
      "icon": "crossword",
      "accent": "#2E6B8A",
      "enabled": true,
      "requires_subscription": false,
      "today": {
        "date": "2026-08-13",
        "theme_title": "Taking the Scenic Route",
        "variants": [
          { "tier": 1, "label": "Warmup",    "status": "solved",      "elapsed_ms": 214000 },
          { "tier": 2, "label": "Daily",     "status": "in_progress", "percent": 0.42 },
          { "tier": 3, "label": "Challenge", "status": "locked",
            "lock_reason": "subscription" }
        ]
      }
    },
    {
      "id": "mini",
      "name": "Mini",
      "today": { "date": "2026-08-13", "variants": [ { "status": "untouched" } ] }
    }
  ]
}
```

## 7.5 Crossword

```http
GET /v1/games/crossword/daily?date=2026-08-13&tier=2
```

```json
{
  "puzzle_id": "0193f2a1-...",
  "date": "2026-08-13",
  "tier": 2,
  "size": { "rows": 15, "cols": 15 },
  "content_url": "https://cdn.braingames.app/p/sha256-9f3c.../puzzle.json",
  "content_hash": "sha256-9f3c...",
  "theme": {
    "title": "Taking the Scenic Route",
    "bundle_url": "https://cdn.braingames.app/t/sha256-a1b2.../",
    "bundle_hash": "sha256-a1b2...",
    "scene_status": "ok"
  },
  "progress": { "...": "..." },
  "aggregates": { "median_ms": 690000, "solve_rate": 0.71 }
}
```

Metadata over the API, body over the CDN. The puzzle body is immutable and content-addressed, so
it's cached forever at every layer and the API response stays small and fast even under a
timezone-edge thundering herd.

```http
GET /v1/games/crossword/archive?cursor=...&limit=30
GET /v1/games/crossword/{puzzle_id}
```

Archive is capped at 30 days for free accounts; the response includes locked entries with a
`lock_reason` so the client can render the paywall in context rather than hiding history.

## 7.6 Progress

```http
PUT   /v1/progress/{puzzle_id}
GET   /v1/progress/{puzzle_id}
POST  /v1/progress/sync
```

`PUT` is the throttled autosave — the client sends at most one every 5 seconds, plus immediately
on blur, completion, and page hide (via `sendBeacon`).

```json
{
  "state": {
    "letters": "SUMMIT..ORE.#....TRAIL#...",
    "pencil": "0000110000000000000000000",
    "checked": [12, 13],
    "revealed": [],
    "cursor": { "r": 3, "c": 7, "dir": "across" }
  },
  "elapsed_ms": 412000,
  "client_revision": 47,
  "device_id": "d-8f2a",
  "used_check": true,
  "used_reveal": false
}
```

Response returns the merged server state and the new `revision`. If the server merged in changes
from another device, `merged: true` is set and the client reconciles — the merge rules are in
[Data model §6.5](06-data-model.md#sync-merge), and correct-letter-beats-empty is the rule that
matters.

`POST /v1/progress/sync` is the bulk endpoint for reconnecting after offline play: it takes an
array of progress records and returns the merged results for each.

```http
POST /v1/progress/{puzzle_id}/complete
```

Explicit completion. Validates the submitted grid server-side against the answer key (cheap, and
the only place we can catch a client bug that congratulates someone on a wrong grid), records the
solve, updates streak and stats, returns the stats card payload.

## 7.7 Stats

```http
GET /v1/stats
GET /v1/stats/history?game=crossword&from=2026-07-01&to=2026-08-13
```

```json
{
  "crossword": {
    "current_streak": 23,
    "longest_streak": 41,
    "total_solves": 187,
    "by_tier": {
      "1": { "solves": 84, "median_ms": 240000, "assist_rate": 0.05 },
      "2": { "solves": 79, "median_ms": 620000, "assist_rate": 0.22 },
      "3": { "solves": 24, "median_ms": 1480000, "assist_rate": 0.58 }
    },
    "suggested_tier": 3,
    "suggestion_reason": "You've solved the last 8 Daily puzzles unassisted, well under the group median."
  }
}
```

## 7.8 Admin

Behind a separate role and a separate rate-limit bucket.

```http
GET   /v1/admin/generation/runs?date=2026-08-13
GET   /v1/admin/generation/runs/{run_id}       # full DAG trace with per-stage timing + tokens
POST  /v1/admin/generation/rerun               # re-run a cohort/date/tier
GET   /v1/admin/review/queue                   # QA sample + flagged puzzles
POST  /v1/admin/review/{puzzle_id}             # approve | reject | annotate
GET   /v1/admin/cohorts
POST  /v1/admin/cohorts/recluster
GET   /v1/admin/costs?from=&to=                # token spend by stage, model, cohort
```

The cost endpoint is not an afterthought. Generation spend is the main variable cost of the
business and it needs to be visible daily, broken down by stage, from week one.

## 7.9 Rate limits

| Bucket | Limit |
|---|---|
| Auth | 10/min per IP |
| Read (puzzles, stats, archive) | 120/min per user |
| Progress write | 30/min per user |
| Profile write | 10/hour per user |
| Admin | 300/min per admin |

Enforced in Redis with a sliding window. `429` responses carry `Retry-After`.

## 7.10 Realtime

None in v1. Progress sync is pull-plus-throttled-push, which is sufficient for a single-player
turn-based game and avoids a websocket tier entirely.

A `GET /v1/events` SSE stream is the reserved path for later — puzzle-ready notifications, live
cohort leaderboards, and eventually co-op solving. Reserving the path costs nothing; building the
tier before there's a feature that needs it costs a lot.
