# 12. Step-by-Step Development Plan

[Doc 10](10-roadmap.md) is the milestone view — what ships when, and what risk each milestone
retires. This is the execution view: the ordered steps, what you build in each, and the test that
says you're done.

Steps are numbered `P<phase>.<step>` and referenced by that ID in issues and commits.

## 12.0 Sequencing principles

Four rules decide the order below. They're worth stating because several steps look
out-of-order until you know them.

1. **Risk first.** The riskiest assumption — that this pipeline produces crosswords real solvers
   respect — gets tested before anything is built on top of it. Phase 2 has no API, no database,
   and no UI for exactly this reason.
2. **Knowledge base before the first prompt.** Phase 1 builds the KB substrate before any agent
   exists. The moment one prompt lives in a Python string, every subsequent prompt will too, and
   the migration cost compounds daily. This is the sequencing consequence of
   [doc 11](11-agent-knowledge-base.md).
3. **Deterministic before probabilistic.** The solver, validators, and QA rules are built and
   tested before the model calls that feed them. When a generated puzzle is bad, you need to
   already know the checker is right.
4. **Vertical slices after the core.** From Phase 4 on, each step goes end to end rather than
   completing a layer. A half-built API with no client is unfalsifiable.

### Dependency graph

```
P0 Foundations
 └─▶ P1 Knowledge base ──────────────┬─────────────────────┐
      └─▶ P2 Puzzle core (no LLM) ───┴─▶ P3 Generation agent │
                                            └─▶ P4 API + data │
                                                 └─▶ P5 Solving UI
                                                      ├─▶ P6 Personalization
                                                      │    └─▶ P7 Theme engine
                                                      └─▶ P8 Platform + Mini
                                                           └─▶ P9 Hardening + launch
```

P6 and P7 are sequential (themes need cohort briefs); P8 runs parallel to P6/P7 from a different
workstream.

---

## Phase 0 — Foundations

*~1 week. One person. Everything after this assumes it.*

### P0.1 Repository skeleton

**Build.** Monorepo: `api/`, `worker/`, `web/`, `knowledge/`, `packages/shared-types/`,
`infra/`. `uv` for Python, `pnpm` for JS. Ruff + mypy strict, ESLint + Prettier, pre-commit hooks.

**Done when.** `make dev` brings up Postgres, Redis, MinIO, api, worker, and web via Docker
Compose, and `make check` runs lint, typecheck, and tests across both languages in under 90
seconds.

### P0.2 CI

**Build.** GitHub Actions: lint → typecheck → unit → integration on every PR. Ephemeral preview
env per PR. Branch protection.

**Done when.** A PR that breaks a type in `packages/shared-types` fails CI in both `api` and
`web`.

### P0.3 Config, secrets, observability skeleton

**Build.** `pydantic-settings` for all config — no literal environment reads scattered through the
code. OpenTelemetry wired to a local collector. Structured JSON logging with correlation IDs.
Anthropic API key handling via the standard credential chain.

**Done when.** A request to `/health` produces a trace with a correlation ID visible in logs, and
a missing required env var fails at startup with a readable message rather than at first use.

> Built ahead of the API in `braingames_core.obs`. Correlation context is stamped onto log
> records by a `logging` record factory rather than read at format time — the formatter can run
> outside the context that created the record (queue handlers, worker threads), and reading it
> late loses the id exactly when it matters. `/health` itself lands with the API in P4.

### P0.4 The generation cost meter

**Build.** A tiny module that wraps every Anthropic call, records `usage` (input, output,
`cache_creation_input_tokens`, `cache_read_input_tokens`) against a stage label, and writes to a
`llm_calls` table. A CLI: `bg costs --from --to --by stage`.

**Done when.** Any model call anywhere in the codebase shows up in `bg costs` within a second.

> Built in week one deliberately. Generation spend is the main variable cost of the business, and
> the cache-hit rate this meter exposes is the canary for the prompt-assembly discipline in
> [§11.5](11-agent-knowledge-base.md#115-prompt-assembly). Retrofitting it after the pipeline
> exists means you spend the first month flying blind on the number that matters most.

---

## Phase 1 — Knowledge base substrate

*~1.5 weeks. Before any prompt exists.*

### P1.1 Document format and loader

**Build.** Frontmatter schema (`id`, `kind`, `applies_to`, `triggers`, `tokens`, `version`,
`status`, `evidence`, `review_by`, `owner`) as a Pydantic model. A loader that reads
`knowledge/**`, validates every document, and fails loudly on a malformed one. `tokens` is
populated by `client.messages.count_tokens` against the target model — measured, never estimated.

**Done when.** `bg kb validate` passes on a seed set of six documents and rejects a document with
a missing `applies_to` naming the file and field.

### P1.2 Router and budgeter

**Build.** `route(stage, ctx) -> list[Doc]` implementing the deterministic rules from
[§11.4](11-agent-knowledge-base.md#114-two-retrieval-modes-and-why-both-exist). Per-stage token
budgets. Sort by `(priority, id)` — deterministic ordering is a correctness requirement here, not
a style preference.

**Done when.** `route()` called twice with identical inputs returns an identical list, and a
property test over shuffled document insertion order confirms it.

### P1.3 Prompt assembler

**Build.** `assemble(stage, ctx) -> list[SystemBlock]` with the cache-block ordering from §11.5:
global spine, routed docs, index, breakpoint. Nothing volatile above the breakpoint.

**Done when.** Two things pass:
- A golden test asserts byte-identical output for the same `(stage, cohort, tier, kb_version)`.
- A **cache-invalidation test**: injecting a timestamp anywhere in the spine causes the test to
  fail with a message naming the offending block. This test exists to catch a change nobody
  intends to make.

### P1.4 The `read_knowledge` tool

**Build.** The agentic-retrieval tool for open-ended stages. `strict: true` schema, `doc_id`
validated against the index, unknown IDs return a structured error listing near matches. Every
read is logged with the generation run ID.

**Done when.** A test agent given the index reads two documents by ID and the reads appear in the
run log.

### P1.5 Eval harness

**Build.** `bg kb eval --doc <id> --against <golden-set>` — runs a frozen scenario set with and
without a document and reports per-metric deltas. Golden sets start empty and grow with each
phase.

**Done when.** The command runs end to end against a stub scenario and prints the delta table
from [§11.7](11-agent-knowledge-base.md#117-the-document-lifecycle).

### P1.6 Seed the core documents

**Build.** Write `core/construction-rules.md`, `core/clue-conventions.md`, and
`core/house-voice.md` — extracted from [doc 01 §1.7](01-product-spec.md#17-what-good-looks-like)
and [doc 04](04-puzzle-generation.md), not written fresh. Plus the `input-handling/` set from
[§11.6](11-agent-knowledge-base.md#116-handling-user-input-for-crossword-themes).

**Done when.** `bg kb validate` passes, spine is under 8k tokens, and `bg kb index --stage
theme_ideation` renders a sensible index.

### P1.7 Lifecycle tooling

**Build.** `bg kb new` scaffolds a valid draft document; `bg kb review` runs the decay pass from
[§11.7](11-agent-knowledge-base.md#117-the-document-lifecycle); `bg kb sync-skills` publishes the
`codegen_*` documents to `.claude/skills/` per [§11.8](11-agent-knowledge-base.md#118-serving-both-agents).

**Done when.** A scaffolded document validates without hand-editing, `bg kb review` reports
overdue and unevidenced documents, and CI fails when `.claude/skills/` is stale.

---

## Phase 2 — Puzzle core, no LLM

*~3 weeks. This phase retires the biggest risk in the product.*

### P2.1 Lexicon

**Build.** Ingest Peter Broda Wordlist and Spread the Wordlist. Normalize, dedupe, score 0–100,
compute `obscurity`, tag by domain. Provenance per entry in `source`.

**Done when.** ~250k entries loaded; spot-checking 100 random entries at each score band matches
a human's judgement of fill quality; every entry has a `source`.

> **Built with public-domain sources instead**, since the named wordlists are not bundled here:
> 370k entries from `words_alpha`, a popular-words list and a frequency-ranked top-10k. They carry
> no multi-word phrases and no human-assigned quality scores, which is the binding constraint on
> tier-3 themed fill — see the measured results at the end of this phase. Swapping in a constructor
> wordlist is a data change: `bg puzzle lexicon build --source-dir <dir>`.
>
> Score and obscurity mean different things and the split is load-bearing. Score is *attestation*
> only. Obscurity is *solver fairness*, and is where entry length and unusual letters belong.
> Putting length into the score instead was a real bug: it moved every long word below the solver's
> floor while changing no candidate ordering, because ordering is per-length already.

### P2.2 Grid template library

**Build.** Programmatic generation of symmetric black-square patterns, filtered by the full
legality checklist: 180° rotational symmetry, all squares checked both directions, min word length
3, connected white space, word count in band. Store with precomputed `theme_slots` and `openness`.

**Done when.** 500+ validated 15×15 templates across three difficulty bands, and a fuzz test that
mutates a valid template in any single cell and confirms the validator catches every resulting
illegality.

### P2.3 The fill solver

**Build.** Backtracking CSP with arc consistency, MRV + degree heuristics, bitset candidate sets
over a precomputed `(length, position, letter)` index. Quality gates on the completed fill.

**Done when.**
- Themed 15×15 fills in under 2s at p50, under 8s at p95.
- Themeless with stacked 15s completes in under 30s.
- Fill success rate above 85% on the first template.
- Quality gates from [§4.5](04-puzzle-generation.md#45-stage-4--fill) enforced, with a test that
  a deliberately obscure×obscure crossing is rejected.

### P2.4 Mechanical QA

**Build.** All fifteen checks from [§4.7](04-puzzle-generation.md#mechanical-code-fast-blocking).
Pure functions over a puzzle object, each returning structured defects.

**Done when.** A test corpus of deliberately broken puzzles — one per check — is caught with the
right defect type, and a corpus of known-good published puzzles passes clean.

### P2.5 CLI

**Build.** `bg puzzle fill --theme-entries ... --tier 2 --out puzzle.json` and
`bg puzzle render puzzle.json` (terminal grid render). No API, no database, no web.

**Done when.** You can produce and eyeball a filled grid from the terminal in one command.

> **Phase gate.** Do not start Phase 3 until P2.3 hits its performance and success numbers. Every
> later phase assumes fills are fast and reliable; if they aren't, that's a research problem and
> you want to be doing research, not building an API on top of a solver that can't fill.

### P2 — measured results

Numbers from the built lexicon (370,301 entries from `words_alpha` + `popular` + `google-10k`)
against a 60-template library, 20 per band. Reproduce with `bg puzzle lexicon build`,
`bg puzzle templates build --per-band 20 --seed 7`, then `bg puzzle fill`.

| Criterion | Target | Measured |
|---|---|---|
| Themeless fill success, first template | > 85% | **90%** (tier 1: 20/20, tier 2: 20/20, tier 3: 14/20) |
| Themeless p50 / p95 | — | 216ms / 12.1s |
| Themed tier 1, template-retry pipeline | 2s p50, 8s p95 | **20/20**, p50 69ms, p95 1.8s |
| Themed tier 2 | 2s p50, 8s p95 | 17/20, p50 458ms, p95 8.8s |
| Themed tier 3 | 2s p50, 8s p95 | **5/20** |
| **Quality gates on completed fills** | enforced | **0 / 52** |

**The solver meets its speed and success targets. The fills it produces are not publishable, and
that is a word-list problem, not a solver problem.** Both failing rows above have one cause.

The gate row is the important one. A median completed fill carries **39 entries out of ~72
attested nowhere but the dictionary** — `PHILOPROGENEITY`, `YASMAK`, `HYLEG`, `NESHLY`, `SNEDS`.
The mean-score gate does not catch this (median 60.3, above every tier floor) because a score of 40
is what "in the dictionary" earns and half the grid earning it still averages respectably. The
obscurity gate catches it exactly.

> That gate read 0.7 until it was measured against a real fill, where nothing tripped it. No short
> word can reach 0.7 on this scale, so the check silently certified grids like the one above as
> having zero obscure entries. The threshold now sits at 0.45, which is where the data separates —
> a familiar-list entry lands at 0.25, a dictionary-only entry at 0.50 — and it lives in
> `lexicon.py` beside the scale it refers to, because the solver and the QA check had each kept
> their own copy and disagreed: `bg puzzle fill` reported 46 unfair crossings on a grid
> `bg puzzle qa` called clean.
>
> The thresholds are deliberately **not** loosened to let current output through. A gate tuned
> until the data passes measures the data, not the puzzle.

Tier-3 themed feasibility has the same root. Raising the per-template budget from 2s to 25s —
twelve times the search — moves it from 3/12 to 4/12, so more search does not buy it. The control
agrees from the other side: the same open grids fill 7/12 with no theme at all. `bg puzzle lexicon
stats` shows why: of 8,847 fifteen-letter entries only 44 are familiar enough for a solver to have
a chance at, and open grids need long entries crossing long entries.

**The fix is one data change.** Single-word dictionaries carry no multi-word phrases
(`SLIPPERYSLOPE`, `ONTHEROCKS`) and no human-assigned quality scores; a constructor wordlist
carries both. The ingest path already accepts phrases and `display` already keeps their spacing, so
this is `bg puzzle lexicon build --source-dir <dir>` and nothing more. Every number in the table
above should be re-measured immediately after.

Two mitigations are cheap and independent of that. `bg puzzle templates vet --drop` fills every
template once and discards the ones this lexicon cannot handle. And `theme_capacity` on each
template reports which theme shapes the grid can hold, so Phase 3's ideation stage gets a
constrained ask instead of proposing a theme found unplaceable at fill time — 19 of 20 themed
failures were instant rejects at ~4ms, so trying another template is nearly free.

**Gate status: the phase gate does not pass.** The deterministic machinery is done and correct:
speed, success rate, template legality, theme placement, and fifteen mechanical checks with a
one-defect-per-check corpus. Phase 3 work that does not depend on fill quality — clue writing
against known-good answers, prompt assembly, the agent loop — can proceed in parallel. **No puzzle
from this lexicon should reach a human solver**, and the M0 exit criterion (30 puzzles reviewed
blind at ≥7/10) cannot be attempted until the wordlist is replaced.

---

## Phase 3 — The generation agent

*~3 weeks. First model calls. Everything reads from the KB.*

### P3.1 Theme ideation

**Build.** The `ThemeConcept` structured-output call from
[§4.2](04-puzzle-generation.md#42-stage-1--theme-ideation). Prompt assembled entirely by
`kb.assemble('theme_ideation', ctx)` — zero prompt text in the Python file.

**Done when.** A grep for a triple-quoted string longer than 200 characters in `worker/` returns
nothing, and this is enforced by a lint rule. 20 consecutive ideation calls across three hand-
written cohort briefs produce valid, non-repeating theme concepts.

### P3.2 Theme validation and the repair loop

**Build.** The length/symmetry/budget validator from
[§4.3](04-puzzle-generation.md#43-stage-2--theme-validation), and the structured-critique retry.

**Done when.** Feeding a deliberately unplaceable entry set produces a critique that names the
specific length mismatch, and the retry succeeds on 80%+ of first failures.

### P3.3 Clue writing

**Build.** Batched clue generation on `claude-sonnet-5` via the Message Batches API. ~20 entries
per request, full grid as context.

**Done when.** Results are assembled **by `custom_id`**, and a test that returns batch results in
reversed order still produces a correct puzzle. (Batch results are explicitly unordered; assembling
positionally produces a puzzle where every clue is attached to the wrong answer, which reads like
a model failure and isn't.)

### P3.4 Editorial QA

**Build.** The `EditorialReview` call on `claude-opus-5`, with the coverage-not-filtering prompt
framing from [§4.7](04-puzzle-generation.md#editorial-one-claude-opus-5-call-advisory--blocking-on-severity).
Defect routing: grid defects to fill, clue defects to targeted re-cluing.

**Done when.** On a corpus of 20 puzzles with known injected defects, recall is above 0.8 at
`major` or higher severity.

### P3.5 The DAG

**Build.** `arq` job graph with per-stage persistence, per-stage retry, and resumption. A
clue-writing failure must not re-run ideation and fill.

**Done when.** Killing the worker mid-DAG and restarting resumes from the last completed stage,
verified by asserting the ideation call happens once.

### P3.6 Golden set + first eval

**Build.** Freeze 40 generation scenarios. Wire the metrics `bg kb eval` needs.

**Done when.** `bg kb eval` produces a real delta table for a real document.

> **Phase gate — the M0 exit criterion.** 30 generated puzzles, reviewed blind by two experienced
> crossword solvers, scoring ≥7/10 on "would you enjoy solving this?" with zero mechanical
> defects. If this fails, stop and iterate on Phases 2–3. This is the moment the product's core
> assumption is tested, and it's cheap to fail here and expensive to fail in month five.

---

## Phase 4 — API and persistence

*~2 weeks.*

| Step | Build | Done when |
|---|---|---|
| **P4.1** | Postgres schema from [doc 06](06-data-model.md); Alembic migrations | `make migrate` from empty to head and back down cleanly |
| **P4.2** | Auth: register, login, refresh rotation with reuse detection, anonymous, in-place upgrade | An anonymous account with a 5-day streak upgrades to registered and keeps the streak — tested explicitly |
| **P4.3** | Puzzle publishing: worker writes JSON to object storage, row to Postgres, content-addressed | Re-running a published cohort/date/tier is a no-op via the unique constraint |
| **P4.4** | `GET /v1/games/crossword/daily` + archive | p95 under 80ms with a warm cache; returns metadata + CDN URLs, not the puzzle body |
| **P4.5** | Progress `PUT`/`GET` and the merge function in `domain/progress.py` | Property test: arbitrary interleaved two-device edit sequences never lose a correct letter and never un-complete a completed puzzle |
| **P4.6** | Evergreen pool: 200 general-audience puzzles per tier, and the fallback path | Deleting today's cohort puzzles still serves a good puzzle, and the fallback-serve metric increments |
| **P4.7** | Generated TS client in CI | A backend schema change breaks the frontend build |

---

## Phase 5 — Solving UI

*~3 weeks. Parallel with the back half of Phase 4.*

| Step | Build | Done when |
|---|---|---|
| **P5.1** | Vite + React + MUI shell, router, providers, default theme | Lighthouse PWA baseline recorded |
| **P5.2** | Grid rendering: DOM cells, memoized, CSS-attribute state | Keystroke → paint under 16ms p95 on a mid-tier Android; a keystroke re-renders ≤3 cells, asserted in test |
| **P5.3** | Input: hidden input, focus management, `beforeinput` interception | **Tested on a real iOS device.** Tapping a cell opens the keyboard on first tap, every time — this is the most common bug in browser crosswords |
| **P5.4** | Navigation: arrows, Tab, Space, clue sync, clue bar | Full solve using keyboard only, no pointer |
| **P5.5** | Pencil, rebus, check/reveal (square/word/puzzle), autocheck, timer with blur-pause | Reducer property test: arbitrary action sequences never produce invalid grid state |
| **P5.6** | Local persistence to IndexedDB, then sync queue | Solve offline in airplane mode, reconnect, state merges with a quiet toast — no silent mutation under the player's hands |
| **P5.7** | Accessibility pass: `role="grid"`, roving tabindex, live regions, non-colour state channels | axe clean on grid, clue list, home; full screen-reader solve completed manually |

> **Phase gate — the M1 exit criterion.** The team solves a generated puzzle daily on their own
> phones for two weeks without wanting to stop.

---

## Phase 6 — Personalization

*~3 weeks.*

### P6.1 Taxonomy and onboarding

**Build.** The ~120-tag taxonomy as versioned data. The four-step onboarding flow from
[§1.2](01-product-spec.md#onboarding-target-under-90-seconds), skippable throughout.

**Done when.** Median completion under 90s with 10 testers; skipping at any step lands in the
general cohort with a working puzzle.

### P6.2 Input handling pipeline

**Build.** The six-stage pipeline from
[§11.6](11-agent-knowledge-base.md#116-handling-user-input-for-crossword-themes): sanitize,
classify (Haiku), policy gate, resolve, reconcile, queue. Every stage's rules come from
`input-handling/` documents.

**Done when.**
- Classification accuracy above 0.85 on a 200-item labelled set.
- **A red-team set of 30 injection attempts in profile free text produces zero prompt
  compromises** — verified by asserting the generated theme is *about* the injected phrase rather
  than obeying it.
- Unmappable inputs land in the review queue.

### P6.3 Embedding and clustering

**Build.** Hybrid representation (tag multi-hot + field + reading/writing + free-text embedding),
HDBSCAN, noise → nearest centroid or general cohort.

**Done when.** On 2,000 synthetic profiles, clusters are human-nameable — a reviewer can look at
each cohort's dominant tags and write a name without hesitating.

### P6.4 Cohort briefs

**Build.** The naming/brief call producing `name`, `clue_voice`, `theme_brief`. Briefs become KB
documents under `domains/` where they're reusable.

**Done when.** Two cohorts with genuinely different briefs produce visibly different puzzles from
the same date and tier — checked by a blind reviewer matching puzzles to briefs above chance.

### P6.5 Migration with hysteresis

**Build.** Fast nearest-centroid recheck on profile edit; weekly full re-cluster; 14-day and
15%-improvement gates; the `PATCH /v1/profile` response shape from
[§7.3](07-api.md#73-profile).

**Done when.** A borderline profile edited repeatedly over four simulated weeks does not
oscillate between cohorts.

### P6.6 Adaptive tier suggestion

**Build.** The rolling-window suggestion from [§3.5](03-personalization-and-cohorts.md#35-difficulty-calibration-within-a-cohort),
using cohort medians from the materialized view.

**Done when.** A simulated fast solver is suggested up within 10 puzzles; a struggling solver is
suggested down within 6. Suggestions are surfaced, never imposed.

> **Phase gate — the M2 exit criterion.** 20 external testers across ≥5 cohorts can *name why* a
> puzzle felt like it was for them. "It felt personalized" is not a pass; "the theme was about
> trail names and 34-Across was BELAY" is.

---

## Phase 7 — Theme engine

*~3 weeks. The security-sensitive phase.*

### P7.1 Tokens

**Build.** The `ThemeTokens` schema, shared between backend Pydantic and frontend Zod. Contrast
validation against WCAG AA with auto-correction. The MUI theme bridge.

**Done when.** A deliberately low-contrast palette is auto-corrected, and one that can't be
corrected is rejected rather than shipped.

### P7.2 CSS validator

**Build.** The PostCSS AST allowlist from [§5.3](05-theme-engine.md#scoped-css). Whole-sheet
rejection on any violation.

**Done when.** A red-team corpus — `url(javascript:)`, `@import`, `position: fixed`, selectors
escaping the scope, `@font-face` with a remote URL — is rejected at 100%, each with a named
reason.

### P7.3 Scene runtime

**Build.** The sandboxed iframe host: `sandbox="allow-scripts"` **without** `allow-same-origin`,
CSP with `connect-src 'none'`, the `@scene/*` module allowlist, the `postMessage` event bridge,
the frame-budget monitor.

**Done when.** A scene that *tries* to `fetch`, read `document.cookie`, or touch `window.parent`
fails in the frame and cannot reach the parent origin — verified by an explicit test that asserts
each attempt throws or is blocked by CSP.

### P7.4 Static checks + build pipeline

**Build.** TypeScript compiler API parse, identifier scan, import allowlist, size cap, esbuild
bundle, headless Playwright smoke test replaying a scripted 60-event solve.

**Done when.** A generated scene with an infinite loop, an oversized bundle, or a console error
in the smoke test never reaches storage.

### P7.5 Theme generation

**Build.** The `GeneratedTheme` call. `templates/scene-*.ts` and `patterns/scene-*.md` from the
KB are what the agent reads via `read_knowledge`.

**Done when.** 50 generated themes: zero validator escapes, zero contrast failures in client,
average scene frame cost under 8ms on a mid-tier Android. **Palette diversity checked explicitly**
— models settle into a house style (warm cream, serif, terracotta) if left open-ended, so measure
that 50 themes aren't 50 variations of one look.

### P7.6 Client integration and downgrade paths

**Build.** `PuzzleThemeProvider`, `SceneFrame`, appearance settings, and every degradation path:
scene fails → tokens + CSS; CSS fails → tokens; tokens fail → default.

**Done when.** Each downgrade path is exercised in an E2E test and the puzzle stays fully
playable in all of them. Player accessibility settings override generated themes in every case.

---

## Phase 8 — Platform

*~3 weeks. Parallel workstream from Phase 6 onward.*

| Step | Build | Done when |
|---|---|---|
| **P8.1** | Cross-device sync: `POST /v1/progress/sync`, queue drain, reconciliation | Two devices solving the same puzzle offline reconcile without losing a letter |
| **P8.2** | PWA: service worker, precache, 14-day puzzle prefetch, `sendBeacon` flush | Installable; full offline solve of a prefetched puzzle |
| **P8.3** | Streaks and stats **in user-local time** | Tested across the date line and both DST transitions. A player in Auckland and one in LA have different days; getting this wrong breaks streaks for half the user base and is invisible from one office |
| **P8.4** | Archive with subscription gating | Locked entries render with `lock_reason` in context rather than being hidden |
| **P8.5** | Subscription, paywall, billing | Purchase → entitlement → all three tiers, end to end in staging |
| **P8.6** | **Mini as the second game** | **Mini ships with zero changes to `shell/`.** A lint rule bans `shell/**` importing `games/**`, and it stays green |

> **Phase gate — the platform claim.** P8.6 is the test of [doc 09](09-multi-game-platform.md).
> If Mini requires shell changes, the game-module contract is wrong, and fixing it now with two
> games is far cheaper than later with six.

---

## Phase 9 — Hardening and launch

*~3 weeks.*

| Step | Build | Done when |
|---|---|---|
| **P9.1** | Load test shaped like the real spike: timezone-midnight thundering herd | p95 under 200ms at 10× projected peak |
| **P9.2** | Alerting: cohort unpublished 4h before local midnight; QA rejection >20%; **fallback-serve rate >2%**; daily spend over budget; cache-read tokens near zero | Each alert fires in a deliberately induced failure |
| **P9.3** | Runbooks: generation failure, cost spike, bad theme in production, KB rollback | An engineer who didn't build the system resolves a simulated incident using only the runbook |
| **P9.4** | KB decay pass #1 | Every seed document has been re-evaluated; unused ones deprecated |
| **P9.5** | Accessibility audit, WCAG 2.2 AA | External audit passes; screen-reader solve completed by someone who uses one daily |
| **P9.6** | Legal: privacy policy covering profile-driven generation, terms, subscription | Reviewed; profile deletion and export verified working end to end |
| **P9.7** | Beta at ~1,000 users | Two weeks stable; D7 retention measured; generation cost per user recorded |

> **P9.2's fallback-serve alert deserves emphasis.** The evergreen pool means generation failures
> are *invisible to players* — which means they'll be invisible to you too unless you alert on the
> fallback rate specifically. That's the one alert that catches silent systemic failure.

---

## 12.1 The first two weeks, concretely

Because "Phase 0, one week" is not a plan you can start on Monday.

| Day | Work |
|---|---|
| 1 | Repo skeleton, Docker Compose, `make dev` green |
| 2 | CI pipeline, pre-commit, branch protection, preview envs |
| 3 | Config, logging, OTel, `/health`. First Anthropic call from a scratch script — verify credentials and the SDK shape end to end |
| 4 | **P0.4 cost meter.** Wrap the call, `llm_calls` table, `bg costs` CLI |
| 5 | KB document schema + loader (P1.1). Two hand-written seed docs |
| 6–7 | Router, budgeter, assembler (P1.2, P1.3). **Write the cache-invalidation test before the assembler** — it's the test that keeps the design honest, and it's easy to skip once the code works |
| 8 | `read_knowledge` tool (P1.4) |
| 9 | Eval harness skeleton (P1.5) |
| 10 | Write the core spine documents (P1.6), extracted from docs 01 and 04 |
| 11–12 | Lexicon ingest and scoring (P2.1) |
| 13–14 | Grid template generation and the legality validator (P2.2) |

By end of week two you have no product and no UI, and you have: a cost meter, a knowledge base
that can't be bypassed, a scored lexicon, and a validated template library. Every one of those is
something that's painful to add later and cheap to add now.

## 12.2 Conventions that keep this on the rails

Four rules, each enforced by a check rather than by discipline. Discipline does not survive
month three.

**1. No prompt text in application code.** A lint rule fails any triple-quoted string over 200
characters in `api/` or `worker/`. Prompts come from `kb.assemble()`, always.

**2. Every model call is schema-constrained and metered.** A single `llm()` wrapper enforces both
— it requires an `output_config.format` and a stage label. Nothing calls the Anthropic SDK
directly.

**3. `shell/**` never imports `games/**`.** Lint-enforced from the day `shell/` exists, not from
the day Mini arrives. It is the highest-leverage guard in the entire codebase and it costs one
config line.

**4. Every generated artifact records its provenance.** `kb_version`, document list, model IDs,
token counts. When quality drops on a Tuesday, you bisect. Without this you guess, and guessing
about model output is how teams lose weeks.

## 12.3 Ways to go faster, and what each costs

If the schedule needs compressing, these are the levers in the order I'd pull them:

| Lever | Saves | Costs |
|---|---|---|
| Ship with 3 hand-written cohorts, defer clustering | ~2 weeks (P6.3–P6.5) | Personalization is a demo, not a system. Fine for a private beta; not for launch |
| Tokens-only themes, defer the scene layer | ~1.5 weeks (P7.3–P7.5) | Loses the delight. Keeps the security posture, since tokens+CSS is the safe half anyway |
| Defer Mini to post-launch | ~1 week (P8.6) | **Don't.** Mini is the test that the platform claim is true. Deferring it means finding out with six games instead of two |
| Defer the archive | ~4 days (P8.4) | Small retention hit; easy to add |
| Buy the grid template library | ~1 week (P2.2) | Licensing cost, provenance questions |

And the one that looks tempting and isn't: **skipping the Phase 3 gate.** Building the API, the
UI, and personalization on top of a pipeline that hasn't been proven to make good crosswords is
how this project fails slowly and expensively instead of quickly and cheaply.
