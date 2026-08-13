# 10. Roadmap, Risks, Open Questions

## 10.1 Milestones

Durations assume a small team (2 backend, 2 frontend, 1 design, plus a part-time crossword editor
from M2). They're sequencing, not commitments.

### M0 — Prove the puzzle (3–4 weeks)

The riskiest assumption in the whole product is that this pipeline produces crosswords a real
solver would respect. Test it before building anything else.

- Grid template library: generate and validate 500 15×15 templates
- Lexicon ingestion and scoring (Broda + Spread the Wordlist + a cohort-vocabulary layer)
- The CSP fill solver, with the quality gates
- Theme ideation and clue writing against three hand-written cohort briefs
- Mechanical QA checks
- CLI only. No API, no frontend, no database.

**Exit criterion:** 30 generated puzzles reviewed blind by two experienced crossword solvers,
scoring ≥ 7/10 on "would you enjoy solving this?" with no mechanical defects. If this fails,
the product needs rethinking and it's better to know in week four than week twenty.

### M1 — Playable end to end (5–6 weeks)

- Postgres schema, FastAPI skeleton, auth
- Nightly generation worker for a single hardcoded cohort
- React grid with full solving UX: navigation, pencil, rebus, check, reveal, timer
- Local progress persistence, no sync
- Default theme only — tokens applied, no generation

**Exit criterion:** the team solves a generated puzzle daily on their phones for two weeks
without wanting to stop.

### M2 — Personalization (4–5 weeks)

- Profile onboarding flow and taxonomy
- Embedding, clustering, cohort creation and naming
- Per-cohort generation across all three tiers
- Cohort briefs as cached prompt prefixes
- Editorial QA pass and the human review queue
- Admin UI: generation runs, review queue, cost dashboard

**Exit criterion:** 20 external testers across ≥ 5 cohorts report their puzzles feel meaningfully
personalized — specifically, that they can name why a puzzle felt like it was for them.

### M3 — Themes (4 weeks)

- Token schema, contrast validation, MUI bridge
- CSS allowlist validator
- The sandboxed scene runtime and its module allowlist
- Theme generation call + validation pipeline + headless smoke test
- Player appearance settings and all the downgrade paths

**Exit criterion:** 50 generated themes, zero security-validator escapes, zero themes that fail
contrast in-client, average scene frame cost under budget on a mid-tier Android.

### M4 — Platform (4 weeks)

- Cross-device sync with the merge rules
- PWA, service worker, offline solving
- Streaks, stats, archive
- Subscription and paywall
- Mini as the second game — the contract test

**Exit criterion:** Mini ships without any change to `shell/`.

### M5 — Launch (4 weeks)

- Load testing at the timezone-midnight spike shape
- Full observability, alerting, on-call runbooks
- Legal: privacy policy covering profile-driven generation, terms, subscription
- Accessibility audit against WCAG 2.2 AA
- Beta at ~1,000 users

### Post-launch

Connections (the real platform test) → Strands → Spelling Bee → Pips. Then: co-op solving,
constructor tools, native wrappers if the PWA proves limiting.

## 10.2 Risks

| Risk | Impact | Likelihood | Response |
|---|---|---|---|
| **Generated puzzles aren't good enough** | Fatal | Medium | M0 exists solely to answer this early. Fallbacks: raise the fill-score floor, expand human review, license human-constructed puzzles for the general cohort while personalized tiers mature |
| **Fill solver too slow for themeless Tier 3** | High | Medium | Template success priors; precomputed bitset indices; overnight generation means seconds of headroom; worst case, curated themeless template subset with known-good fill characteristics |
| **Cohorts feel generic** | High | Medium | `min_cluster_size` is a tuning dial. If cohorts are too broad, lower it and accept the cost. Cost per cohort is small enough that this is affordable |
| **Generation cost scales worse than modelled** | Medium | Medium | Cost dashboard from M2, not launch. Levers in [§4.8](04-puzzle-generation.md#48-cost-model) are ordered by size |
| **A generated theme ships an exploit** | Severe | Low | Two-layer design; sandbox without `allow-same-origin`; CSP with `connect-src 'none'`; static checks; headless smoke test. The sandbox is the real control — everything else is defence in depth |
| **A theme makes a puzzle unreadable** | Medium | Medium | Contrast validated at generation and re-validated in client; player override always wins; visual regression tests |
| **Prompt injection via profile free text** | Medium | Medium | Delimited data blocks with explicit data-not-instructions framing; length caps; taxonomy mapping means free text is a small, low-weight input |
| **Streak bugs at timezone boundaries** | Medium (loud) | High | Streaks computed in user-local time, tested explicitly across the date line and DST transitions |
| **NYT similarity concerns** | Medium | Low | Grid patterns and crossword conventions aren't protectable; no NYT content, branding, or trade dress is used. Distinct visual identity from the start |
| **Wordlist licensing** | Medium | Low | Use only permissively licensed lists; record provenance per entry in the `source` column |

The two worth restating: **M0 answering the quality question early** is the single most important
scheduling decision in this plan, and **the theme sandbox** is the one place where a security
shortcut would be catastrophic and tempting.

## 10.3 Open questions

Genuine decisions, not rhetorical ones. Each needs an owner and a date.

**1. How large should a cohort be?** `min_cluster_size = 150` is a starting guess balancing cost
against how personal a puzzle feels. Only real profile data answers it. *Resolve in M2 with
tester feedback across a range of sizes.*

**2. Should the three tiers share a theme?** Sharing makes "today's theme" a coherent unit and is
cheaper. Independent themes give more variety. The current design shares the *concept* and varies
the execution. *Resolve in M1 with the team's own daily solving.*

**3. Human editorial review — how much?** 5% sampling is a guess. It could need to be 100% at
launch and taper. This is the largest non-model cost in the system and it deserves a real
staffing answer. *Resolve in M2.*

**4. Is a themeless Tier 3 right?** NYT Friday/Saturday are themeless and that's what expert
solvers expect. But a themeless puzzle loses the personalization signal that the theme carries.
Alternative: themed Tier 3 with much harder cluing. *Resolve in M0 with solver feedback.*

**5. Free tier boundary.** Warmup-only is one cut. "All tiers, 7-day archive" is another and may
convert better. *Resolve pre-launch with pricing research.*

**6. Notifications.** A daily "your puzzles are ready" push is the standard retention mechanic and
also the standard way to annoy people. Opt-in, timing configurable, and probably not in v1.

**7. Do themes need a picker?** Currently the theme is bound to the puzzle. Players may want to
override — either for accessibility or preference. A theme picker is easy given the bundle
architecture but it dilutes the "the puzzle is themed" premise. *Resolve in M3.*

## 10.4 What must be true for this to work

Compressed to four claims. If any is false, the product is in trouble, and each is testable early:

1. **The pipeline produces crosswords real solvers respect.** Tested in M0.
2. **Cohort-level personalization is perceptibly personal.** Tested in M2.
3. **Generated themes are safe and beautiful, automatically, at daily cadence.** Tested in M3.
4. **The economics hold** — under a cent per user per month on generation at scale. Measured
   continuously from M2.

The plan is sequenced so each claim is tested at the earliest point it can be, with the cheapest
artifact that can test it. That ordering is the plan's actual content; the week counts are
scaffolding around it.
