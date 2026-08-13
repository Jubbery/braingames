# 9. Multi-Game Platform

> "Later game development involves games like Pips, Mini, Spelling Bee, Connections, Strands,
> etc. So it is important to keep that in mind, this platform will not only have crossword."

The way this requirement is usually failed is not by forgetting it — it's by building crossword
with crossword-shaped abstractions and discovering, when Connections arrives, that "the daily
puzzle" means a 15×15 grid everywhere in the codebase. So the abstraction is stated up front and
crossword is built as its first implementation, not as the thing everything else has to imitate.

## 9.1 What the games have in common

Look at the NYT games catalog and the shared substrate is almost all of the engineering:

| Concern | Crossword | Mini | Connections | Strands | Spelling Bee | Pips |
|---|---|---|---|---|---|---|
| One puzzle per day | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Local-midnight drop | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Resumable progress | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cross-device sync | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Streaks & stats | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Archive | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Shareable result | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Themed visuals | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Personalizable** | ✓ | ✓ | ✓ | ✓ | ✓ | partly |
| Difficulty tiers | ✓ | – | – | – | – | ✓ |
| Grid geometry | square | square | none | square | hex | polyomino |

Eight rows of "identical" and three of "different". The shell owns the eight; a game module owns
the three. That ratio is the whole argument for the structure below.

## 9.2 The game module contract

### Backend

Each game registers a module implementing a single protocol:

```python
class GameModule(Protocol):
    id: str                        # 'crossword'
    display_name: str
    variants: list[VariantSpec]    # crossword: 3 tiers; connections: single
    supports_personalization: bool
    puzzle_schema: type[BaseModel]

    async def generate(self, cohort: Cohort, date: date,
                       variant: VariantSpec) -> GeneratedPuzzle: ...

    def validate(self, puzzle: BaseModel) -> ValidationResult: ...

    def merge_progress(self, a: ProgressState, b: ProgressState) -> ProgressState: ...

    def score(self, puzzle: BaseModel, progress: ProgressState) -> SolveResult: ...

    def share_text(self, puzzle: BaseModel, result: SolveResult) -> str: ...
```

Registration is a line in a registry. Everything else — the nightly sweep, publishing, serving,
progress storage, sync, streak accounting, archive, admin review — is written against
`GameModule` and has no idea crossword exists.

Two of these methods deserve comment because they're the ones people assume are shared and
aren't:

- `merge_progress` is per-game because conflict semantics differ. Crossword's rule is per-cell
  last-writer-wins with correct-beats-empty. Connections is an ordered guess list, where the
  merge is "union the guesses, preserve order, cap at four mistakes." Getting Connections' merge
  wrong loses a player's mistake count, which is the whole game.
- `share_text` is per-game because the emoji-grid share is a genuinely game-specific artifact and
  a major growth channel. It's cheap to specify and expensive to retrofit.

### Frontend

```typescript
export interface GameModule<TPuzzle, TState> {
  id: string;
  displayName: string;
  icon: ComponentType;
  accent: string;

  Component: LazyExoticComponent<ComponentType<GameProps<TPuzzle, TState>>>;

  createInitialState(puzzle: TPuzzle): TState;
  reducer(state: TState, action: GameAction): TState;
  isComplete(puzzle: TPuzzle, state: TState): boolean;
  serialize(state: TState): SerializedProgress;
  deserialize(data: SerializedProgress): TState;

  /** Game events emitted to the theme scene layer. */
  themeEvents(prev: TState, next: TState, puzzle: TPuzzle): GameEvent[];
}
```

`themeEvents` is how theming generalizes. Crossword emits `word.solved`; Connections emits
`group.found`; Spelling Bee emits `pangram.found`. A theme scene subscribes to a small shared
event vocabulary and games map their internal state transitions onto it. The theme engine never
learns any game's rules.

## 9.3 What the shell owns

```
shell/
  auth            sessions, tokens, anonymous upgrade
  profile         the puzzle profile and cohort assignment — shared across ALL games
  daily           local-midnight resolution, drop scheduling, timezone handling
  progress        storage, sync queue, conflict orchestration (delegating the merge)
  stats           streaks, per-game aggregates, the adaptive-difficulty engine
  archive         history, pagination, subscription gating
  theming         token application, CSS injection, the sandboxed scene host
  navigation      home tile grid, game shell chrome, settings
  offline         service worker, IndexedDB, prefetch
  share           the share sheet; games supply the text
```

The profile is deliberately shared. A player's interests should theme their Connections
categories, their Strands spangram, and their Spelling Bee letter set as much as their crossword.
One profile, one cohort, many games — that's the platform's actual product advantage over a
collection of separate puzzle apps.

## 9.4 How the other games map on

Sketches only, but concrete enough to prove the abstraction holds.

**Mini (5×5 crossword).** Same module, different config. `variants` is a single untiered entry,
size 5, no theme entries, a Haiku-tier clue-writing call. Ships within days of crossword.

**Connections.** 16 words, four groups of four, ascending difficulty. Personalization is unusually
natural: a cohort's interest tags directly seed category concepts (`outdoors.climbing` →
"CLIMBING HOLDS: CRIMP, JUG, SLOPER, PINCH"). Generation is one structured LLM call plus a
validation pass whose real job is the hard part — checking that no word plausibly belongs to two
groups, which is what separates a good Connections from an unfair one. Progress state is an
ordered guess list; merge is union-preserving-order.

**Strands.** A word-search over a themed word set with a spangram crossing the grid. Generation
is theme → word set (LLM) → grid placement (a solver, same family as the crossword fill). Reuses
the grid rendering with a path-drawing interaction layer instead of cell entry.

**Spelling Bee.** Seven letters, one central, all words must use it; find a pangram. Generation
is almost entirely lexicon work — pick a pangram whose letter set yields a good word count — with
the LLM used only to bias pangram selection toward the cohort's vocabulary. Hex layout is the one
genuinely new piece of rendering.

**Pips.** Domino-placement logic over a polyomino board with region constraints. The odd one out:
it's a constraint puzzle rather than a word puzzle, so personalization applies to *visual* theme
and difficulty rather than content. Generation is a constraint solver plus a uniqueness check —
no LLM in the core loop at all. Good proof that the module contract isn't secretly
word-game-shaped.

## 9.5 Build order and why

1. **Crossword** — the flagship, and the hardest. Building it first forces the shell abstractions
   to be real rather than aspirational.
2. **Mini** — near-zero marginal cost, and the first genuine test that the module contract works.
   If Mini requires shell changes, the contract is wrong and it's cheap to find out now.
3. **Connections** — the first structurally different game. Validates that `merge_progress`,
   `share_text`, and `themeEvents` were the right seams.
4. **Strands / Spelling Bee** — reuse grid rendering and lexicon infrastructure respectively.
5. **Pips** — validates that a non-word, non-LLM game fits without special-casing.

The step that matters is #3. If Connections lands without touching the shell, the platform claim
is true. If it doesn't, the abstraction gets fixed while there are two games to migrate instead
of six.

## 9.6 The failure mode to actively avoid

Crossword-specific concepts leaking into shell code. Concretely, these belong in
`games/crossword/` and nowhere else:

- The word "grid" in a shell type name
- `tier` as a shell-level concept (it's a `VariantSpec`, and most games have one variant)
- Across/Down anywhere outside the crossword module
- Cell coordinates in the progress table's *schema* — `state` is opaque JSONB to the shell, and
  only the game module knows what's inside it
- Any shell import from `games/crossword/`

That last one is enforceable. A lint rule banning `shell/**` from importing `games/**` costs
nothing and is the single highest-leverage guard on this whole design.
