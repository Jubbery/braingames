# 1. Product Spec

## 1.1 The pitch

The New York Times crossword is a shared cultural object: everyone gets the same grid, and the
difficulty ramps Monday to Saturday. That's its strength and its ceiling. A Monday puzzle about
Broadway musicals lands beautifully for theatre people and bounces off everyone else, and a
beginner who wants a Saturday-hard puzzle has to wait until Saturday.

Braingames keeps the craft — symmetric grids, wordplay themes, fair crossings, that specific
NYT clue voice — and moves two dials:

- **Themes come from what you actually care about.** Rock climbing, veterinary medicine, Norse
  mythology, competitive baking, sci-fi paperbacks.
- **All three difficulties are available every day.** You pick your Monday or your Saturday.

## 1.2 Player journey

### Onboarding (target: under 90 seconds)

A four-step flow. Skippable at any point — a skipped profile gets the general-audience cohort,
which is a perfectly good crossword.

**Step 1 — Interests.** A chip cloud of ~120 curated interest tags across 12 domains (Sports &
Outdoors, Music, Film & TV, Food & Drink, Science, History, Travel, Gaming, Literature, Art &
Design, Tech, Nature). Search is available; free-text entry is allowed and normalized into the
taxonomy on the backend. Pick 3–10.

**Step 2 — Work.** One dropdown for professional field (~40 options) plus an optional free-text
role. Feeds domain vocabulary — a nurse is comfortable with `TRIAGE` and `STAT` at a difficulty
where a general solver isn't.

**Step 3 — Reading & writing.** Three quick questions:

- What do you read? (literary fiction / genre fiction / non-fiction / news & essays / poetry /
  technical / comics / not much)
- Do you write? (professionally / creatively / occasionally / no)
- How do you feel about wordplay? (love puns / enjoy clever misdirection / prefer straightforward
  clues)

This is the single most useful signal for **clue voice**. A poetry-and-puns player gets clues
leaning on double meanings; a technical-non-fiction player gets more definitional cluing with
precise wording.

**Step 4 — Calibration.** A 5×5 Mini. Solve time plus a self-report ("that was easy / about right
/ hard") sets the starting difficulty anchor. Purely for tier defaults; skippable.

### The daily drop

Puzzles unlock at **local midnight** in the player's timezone. Not a fixed ET drop — there's no
shared-leaderboard reason to synchronize, and a local-midnight drop is strictly friendlier.

The home screen is a grid of game tiles (see [Multi-game platform](09-multi-game-platform.md)).
The Crossword tile shows today's theme title, the three tiers, and per-tier state (untouched /
in progress with a % ring / solved with a check and time).

### The three tiers

Each tier is a **different puzzle**, not the same grid re-clued. Same theme concept where the
theme supports it; independent grid, fill, and clues.

| Tier | Name | NYT analogue | Grid | Theme | Clue style |
|---|---|---|---|---|---|
| 1 | **Warmup** | Monday–Tuesday | 15×15, 74–78 words | Explicit, often with a revealer that states the trick | Direct definitions, common crossings, no obscurities at crossings |
| 2 | **Daily** | Wednesday–Thursday | 15×15, 72–78 words | Wordplay-forward; the trick is discoverable, not stated | Misdirection, some `?` clues, moderate proper nouns |
| 3 | **Challenge** | Friday–Saturday | 15×15, 68–72 words, wide-open corners | Usually themeless, with stacked long entries drawn from the cohort's domain | Dense misdirection, multi-word entries, colloquial phrases |

The tier difficulty is calibrated **relative to the cohort's own vocabulary**. A Challenge puzzle
for the marine-biology cohort may use `NUDIBRANCH`, and that is fair for them and would not be
for a general solver. This is the core insight that makes personalization more than a coat of
paint: personalization expands the fair-play vocabulary, which is what actually makes hard
puzzles enjoyable rather than punishing.

### Solving

Table stakes, all of it, because NYT solvers will notice any of it missing:

- Tap/click a cell to select; tap again to toggle direction. Arrow keys navigate; Tab/Shift-Tab
  jumps clue to clue; Space toggles direction.
- Current clue is pinned above the grid on mobile, and the clue lists highlight in sync.
- The active word is highlighted; the active cell is a stronger highlight.
- **Pencil mode** (tentative entries render grey/italic).
- **Rebus** entry (multiple characters in one square) — needed for Tier 2 and 3 themes.
- **Check** square / word / puzzle. **Reveal** square / word / puzzle. Revealing marks the solve
  as assisted in stats, visibly and without judgement.
- **Autocheck** toggle: wrong letters mark immediately.
- **Timer** with pause; auto-pauses on tab blur.
- Full solve triggers the theme's celebration animation and a stats card.

### Post-solve

A stats card: time, tier, whether it was assisted, current streak, this puzzle's median time
across the cohort, and a one-paragraph **"about this theme"** note written by the constructor
agent — what the trick was, why these entries. This note does a lot of work for the personalized
premise: it makes the puzzle feel authored rather than emitted.

## 1.3 Preferences are editable, always

Profile edits are a first-class flow (Settings → Puzzle Preferences), not buried onboarding. The
UI must set the right expectation about timing:

- Edits take effect for **tomorrow's** puzzles. Today's are already generated.
- If an edit moves the player to a different cohort, that's stated plainly: *"You'll be getting
  puzzles from a new group starting tomorrow."*
- If the new cohort has already-generated puzzles for today, they're offered immediately as a
  bonus — a nice moment, and cheap because the puzzles exist.

An explicit **"more like this / less like this"** control on the post-solve card feeds implicit
preference signal without asking the player to reopen a settings form.

## 1.4 Streaks, stats, archive

- **Streak**: one solve of any tier on a given local day extends it. Deliberately generous —
  streak anxiety is a churn driver, and being able to bank a Warmup on a bad day is the
  release valve.
- **Stats**: per-tier solve counts, best and average times, assist rate, a solve-time trend, a
  distribution of themes solved by domain.
- **Archive**: all previously generated puzzles for the player's cohort, back to the cohort's
  creation. Free tier gets 30 days; full archive is a subscription feature.

## 1.5 Free vs. subscription

| | Free | Subscription |
|---|---|---|
| Daily crossword | Warmup tier only | All three tiers |
| Mini / lighter games | Full | Full |
| Archive | 30 days | Everything |
| Stats | Basic | Full trends and breakdowns |
| Themes | Standard visual themes | All generated visual themes |
| Profile | Standard cohort assignment | Cohort assignment + fine-grained tuning controls |

## 1.6 Explicit non-goals for v1

- No user-constructed puzzles or sharing of constructions. Big feature, different product.
- No real-time competitive or co-op solving. Async leaderboards only.
- No native mobile apps. The PWA is installable and that's enough for v1.
- No non-English languages. The pipeline generalizes; the wordlists and clue-voice tuning do not.
- No per-user unique puzzles. Cohort-level is the design point, not a limitation to route around
  later. See [Personalization & cohorts](03-personalization-and-cohorts.md).

## 1.7 What "good" looks like

The quality bar the pipeline is measured against, restated as a checklist because it is the
thing most likely to be quietly compromised:

- Grids have 180° rotational symmetry, every square is checked by both an Across and a Down
  entry, and no entry is shorter than 3 letters.
- No entry appears twice in a puzzle; no substantial word in a clue appears in its own answer.
- Every theme entry pair is symmetrically placed.
- No two obscure entries cross at a letter a solver couldn't infer.
- Clues agree with answers in tense, number, and part of speech. Abbreviations are signalled.
  Foreign words are signalled.
- A player who finishes the puzzle can say what the theme *was*.

If we ship puzzles that fail this list, personalization is worthless — it's a gimmick attached to
a bad crossword. Everything in [Puzzle generation](04-puzzle-generation.md) is downstream of
enforcing it mechanically rather than hoping for it.
