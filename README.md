# Braingames

A NYT-style games platform whose flagship product is a **personalized daily crossword**.

Players describe their hobbies, their work, and how they read and write. That profile drives an
AI generation pipeline that produces NYT-quality crosswords — themed, cleanly filled, fairly
clued — in **three difficulty tiers every day**. The theme doesn't stop at the words: the agent
also generates the *visual* theme (palette, typography, animated motifs) that the grid is
rendered in.

The platform is built from day one as a **games shell**, not a crossword app. Crossword is the
first tile on the home screen; Mini, Connections, Strands, Spelling Bee, Pips and friends slot
into the same shell without re-plumbing auth, streaks, stats, archive, or sync.

---

## Documents

| # | Document | What's in it |
|---|---|---|
| 1 | [Product spec](docs/01-product-spec.md) | What a player experiences: onboarding, the daily drop, the three tiers, solving UX, streaks and stats |
| 2 | [Architecture](docs/02-architecture.md) | Services, the chosen stack and why, request/generation flows, deployment |
| 3 | [Personalization & cohorts](docs/03-personalization-and-cohorts.md) | Profile schema, interest taxonomy, embedding + clustering, why similar players share puzzles |
| 4 | [Puzzle generation](docs/04-puzzle-generation.md) | The seven-stage pipeline: theme → grid → fill → clue → QA. Model choice, prompts, cost |
| 5 | [Theme engine](docs/05-theme-engine.md) | How an agent safely ships animated HTML/CSS/TS themes into the client |
| 6 | [Data model](docs/06-data-model.md) | Postgres schema, puzzle JSON format, progress and sync records |
| 7 | [API](docs/07-api.md) | REST surface, auth, sync semantics, error contract |
| 8 | [Frontend](docs/08-frontend.md) | React/Vite/MUI structure, the grid engine, offline-first solving, accessibility |
| 9 | [Multi-game platform](docs/09-multi-game-platform.md) | The game-module contract that makes Mini/Connections/Strands drop-in |
| 10 | [Roadmap](docs/10-roadmap.md) | Milestones, staffing shape, risks, open questions |

---

## The stack, in one table

| Layer | Choice | One-line reason |
|---|---|---|
| Frontend | React 19 + TypeScript, Vite, MUI v7 | Specified. MUI's theming primitives are what the AI-generated themes plug into |
| Client state | Zustand (game state) + TanStack Query (server state) | Game state is a tight reducer; server state wants caching and revalidation |
| Animation | `motion` (Motion for React) + CSS `@keyframes` + inline SVG | An LLM writes these fluently; no binary formats it can't author |
| Offline | IndexedDB (`idb`) + `vite-plugin-pwa` | Solving must never lose a keystroke on a subway |
| Backend | **FastAPI (Python 3.12) + Pydantic v2** | See below |
| Jobs | `arq` (async, Redis-backed) | Nightly batch generation; lightweight next to Celery |
| Database | Postgres 17 + `pgvector` | Relational data and profile embeddings in one place |
| Cache/locks | Redis | Session cache, rate limits, generation locks, leaderboards |
| Object storage | S3-compatible (R2) | Theme bundles, generated assets |
| AI | Anthropic API — `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5` | Structured outputs, Batch API, prompt caching all map onto this pipeline |

### Why FastAPI and not a TypeScript backend

The user asked us to choose. A same-language monorepo is genuinely attractive, and we give it up
for three concrete reasons:

1. **Grid filling is a constraint-satisfaction problem, not CRUD.** The core of puzzle generation
   is a backtracking search with arc consistency over a 200k-word scored lexicon. Python's
   ecosystem for this (plus NumPy for the bitset operations that make it fast) is materially
   better, and the code is the kind that gets rewritten often during tuning.
2. **The AI pipeline is the product.** Pydantic models double as the Anthropic structured-output
   schemas *and* the API response models — one definition, validated end to end. That's a real
   reduction in glue code for a system where every LLM call is schema-constrained.
3. **Type safety across the boundary is recoverable.** FastAPI emits OpenAPI; we generate a typed
   TS client with `openapi-typescript` + `openapi-fetch` in CI. The frontend gets the same
   compile-time guarantees a shared-types monorepo would give it.

If the team is TypeScript-only and hiring reflects that, NestJS + Fastify with a Python sidecar
for the solver is a legitimate variant — the service boundary in
[Architecture](docs/02-architecture.md) is drawn so that swap costs one service, not the system.

---

## The three ideas this design rests on

**1. Generate per cohort, not per user.** Puzzle generation is expensive and quality-sensitive;
per-user generation is both unaffordable and unreviewable. Profiles are embedded and clustered
into a few hundred **cohorts**. Each cohort gets 3 puzzles a day. A cohort of 400 hikers-who-code
shares a puzzle, which is exactly the "similar profiled users receive similar if not the same
themed crosswords" requirement — arrived at as an architectural consequence rather than a
special case.

**2. The grid is chosen, not generated.** LLMs are excellent at theme ideation and cluing, and
unreliable at producing NYT-legal grids (180° symmetry, ≤78 words, full interlock, no unchecked
squares). So the model picks theme entries; a solver picks a hand-vetted grid template that fits
those entries' lengths and fills it deterministically. Creativity where models are strong,
determinism where they aren't.

**3. Generated theme code is untrusted code.** An agent writing CSS and TypeScript that runs in
other people's browsers is a supply-chain problem wearing a feature's clothes. Themes are split
into a validated declarative layer (safe, styles the grid itself) and a sandboxed executable
layer (an `iframe` with a `postMessage` bridge, decorative only). See
[Theme engine](docs/05-theme-engine.md).

---

## Status

Design phase. No implementation yet — these documents are the spec that implementation should be
reviewed against.
