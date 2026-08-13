# 8. Frontend

React 19 + TypeScript on Vite, MUI v7 for the component layer. Installable PWA. Offline-first
for solving.

## 8.1 Structure

```
web/src/
├── app/
│   ├── App.tsx  router.tsx  providers.tsx
│   └── theme/                     MUI theme factory, token → MUI bridge
├── shell/                         everything a game can assume exists
│   ├── HomeScreen.tsx             the game tile grid
│   ├── GameShell.tsx              header, timer, menu, settings — wraps every game
│   ├── StatsCard.tsx  StreakBanner.tsx  ArchiveList.tsx
│   └── registry.ts                the game registry (see doc 09)
├── games/
│   ├── crossword/
│   │   ├── CrosswordGame.tsx      the module's entry point
│   │   ├── grid/                  Grid, Cell, GridInput, Cursor
│   │   ├── clues/                 ClueList, ClueBar, ClueNavigator
│   │   ├── state/                 reducer, selectors, keyboard map
│   │   └── engine/                pure logic: navigation, checking, rebus
│   └── mini/                      reuses crossword/* with a 5×5 config
├── theming/
│   ├── ThemeProvider.tsx          token application + CSS injection
│   ├── SceneFrame.tsx             the sandboxed iframe host
│   └── tokens.ts                  schema + validation (mirrors the backend)
├── data/
│   ├── api/                       generated client + typed hooks
│   ├── offline/                   IndexedDB, sync queue, conflict resolution
│   └── stores/                    Zustand: session, settings, active game
└── lib/
```

**State split, deliberately:**

- **Zustand** for in-game state. The crossword reducer runs on every keystroke; it wants to be a
  tight synchronous function with no async machinery in the way.
- **TanStack Query** for server state. Puzzle metadata, stats, archive — things with caching,
  staleness, and revalidation semantics that Query already solves.

Mixing these concerns is the standard way this kind of app becomes slow. Keystroke handling must
never touch a query cache.

## 8.2 The grid

**DOM, not canvas.** A 15×15 grid is 225 elements — well within React's comfort zone — and the
DOM buys three things canvas would cost real work to replicate:

- Screen reader access. Each cell is a real focusable element with real ARIA.
- Text selection, browser zoom, and OS-level accessibility tooling all just work.
- The theme system styles cells with CSS custom properties. On canvas, every theme would have to
  reimplement cell rendering.

```
<div role="grid" aria-label="Crossword grid, 15 by 15" data-theme-scope="t-a1b2">
  <div role="row">
    <div role="gridcell"
         aria-label="1 Across, S U M M I T. Clue: Peak achievement?"
         data-state="filled selected"
         tabindex="-1">
      <span class="cell-number">1</span>
      <span class="cell-letter">S</span>
    </div>
```

Rendering discipline, because this is where a crossword app gets janky:

- `Cell` is `React.memo`'d on a narrow prop set (`letter`, `state`, `number`). A keystroke
  re-renders 1–3 cells, not 225.
- Cell state is a CSS-attribute string, so highlight changes are style recalcs rather than React
  re-renders. Moving the cursor across a 15-letter word touches no React state for the
  highlighting.
- The grid is `content-visibility: auto` off-screen on mobile.

### Input

Mobile keyboards are the hard part. The approach that actually works:

- A single hidden `<input>` positioned under the selected cell, `inputMode="text"`,
  `autocapitalize="characters"`, `autocorrect="off"`, `spellcheck={false}`.
- Focus follows the selected cell. `beforeinput` is intercepted; the grid state is the source of
  truth and the input's own value is cleared every frame.
- iOS Safari needs the input to be focused inside the same user-gesture task as the tap, or the
  keyboard won't open. This is the single most common bug in browser crosswords.
- A custom on-screen keyboard is offered as a setting for players who prefer it, with a rebus key
  and a pencil toggle.

### Keyboard map (desktop)

| Key | Action |
|---|---|
| A–Z | enter letter, advance within the word |
| Backspace | clear and retreat |
| Space | toggle direction |
| Arrows | move; perpendicular arrow switches direction |
| Tab / Shift-Tab | next / previous entry |
| Enter | next entry |
| `.` | toggle pencil mode |
| `Esc` | open rebus entry for the current cell |
| `Ctrl/Cmd+K` | command palette (check, reveal, settings) |

## 8.3 Offline-first solving

Solving must survive a tunnel. The rule is: **a keystroke is never lost, and never waits on the
network.**

```
keystroke
   │
   ├─▶ Zustand reducer            (synchronous, immediate render)
   ├─▶ IndexedDB write            (debounced 300ms — the durable local record)
   └─▶ sync queue                 (debounced 5s → PUT /v1/progress/{id})
                                  offline: queued, retried with backoff on reconnect
```

- Service worker (`vite-plugin-pwa`, Workbox): app shell precached; puzzle JSON and theme bundles
  cache-first with a 60-day expiry; API `GET`s network-first with a cache fallback.
- The last 14 days of puzzles are prefetched on Wi-Fi so the archive works offline too.
- On reconnect, the queue drains oldest-first and the merged server state reconciles into the
  store. If the merge changed anything, a quiet toast says so rather than silently mutating the
  grid under the player's hands.
- `visibilitychange` → `navigator.sendBeacon` flushes progress on tab close.

## 8.4 Theme integration

Three layers land in the client in order of increasing risk and decreasing importance, so each
can fail independently:

```tsx
function PuzzleThemeProvider({ theme, children }: Props) {
  const { tokens, css, sceneUrl, sceneStatus } = useThemeBundle(theme.bundleUrl);
  const settings = useSettings();

  // 1. Tokens → CSS custom properties + MUI theme.  Always applied.
  const muiTheme = useMemo(() => buildMuiTheme(tokens, settings), [tokens, settings]);
  const scopeId = `t-${theme.bundleHash.slice(0, 8)}`;

  return (
    <MuiThemeProvider theme={muiTheme}>
      <div data-theme-scope={scopeId} style={tokensToCssVars(tokens)}>
        {/* 2. Scoped CSS module.  Skipped if validation downgraded the theme. */}
        {css && settings.themes === 'full' && <style>{css}</style>}

        {/* 3. Sandboxed scene.  Lazy, after first paint, never on the critical path. */}
        {sceneStatus === 'ok' && settings.themes === 'full' && !settings.reducedMotion && (
          <SceneFrame src={sceneUrl} tokens={tokens} />
        )}

        {children}
      </div>
    </MuiThemeProvider>
  );
}
```

`SceneFrame` mounts the sandboxed iframe described in
[Theme engine §5.4](05-theme-engine.md#54-layer-b--the-theme-scene), forwards `GameEvent`s over
`postMessage`, and unmounts the frame if its frame budget is exceeded. It is positioned
absolutely behind the grid with `pointer-events: none` — the scene is decoration and can never
intercept an input.

Player settings override generated themes in every case. High contrast and reduced motion are not
negotiable by a theme.

## 8.5 Accessibility

Non-negotiable, and cheap if built in from the start:

- The grid is a real `role="grid"` with roving `tabindex`, and every cell announces its position,
  its entry numbers, and the active clue.
- Clue lists are `role="listbox"` with the current clue as the active option; selecting a clue
  moves the grid cursor.
- Live region announces entry changes, check results, and completion.
- Every state distinction that uses colour also uses a second channel — error cells get a
  diagonal slash, theme cells get a corner mark, pencil entries are italic. Colour alone never
  carries meaning.
- Contrast is validated at theme generation and re-validated in the client; a theme that fails
  in-client is downgraded on the spot.
- Full keyboard operation. No pointer-only affordances anywhere.
- `prefers-reduced-motion` disables the scene layer and reduces cell animations to opacity fades.
- Target: WCAG 2.2 AA, with automated axe checks in CI on the grid, the clue list, and the home
  screen.

## 8.6 Performance budget

| Metric | Target |
|---|---|
| LCP (grid interactive), 4G, mid-tier Android | < 2.0s |
| Keystroke → paint | < 16ms p95 |
| Initial JS (app shell + crossword) | < 180KB gzipped |
| Theme bundle | < 48KB gzipped |
| Scene frame cost | < 8ms average, hard-stopped at 16ms |
| Lighthouse PWA | 100 |

Route-level code splitting per game (`games/crossword` is a lazy chunk), MUI imported per
component, the scene layer loaded only after the grid is interactive.

## 8.7 Testing

- **Unit** (Vitest): the crossword reducer, navigation, rebus handling, progress merge, token
  validation. The reducer gets property tests — arbitrary keystroke sequences must never produce
  an invalid grid state.
- **Component** (Testing Library): the grid renders and navigates via keyboard only; clue sync;
  check/reveal behaviour.
- **E2E** (Playwright): full solve on desktop and mobile emulation; offline solve → reconnect →
  merge; theme downgrade paths; iOS keyboard focus behaviour.
- **Visual** (Playwright screenshots): a fixed reference puzzle rendered under a set of stored
  theme bundles, light and dark, so a theme-engine regression is caught by a diff rather than by
  a player.
