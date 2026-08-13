---
id: theme-engine-contract
kind: core
applies_to:
  - theme_scene_generation
summary: >
  The contract generated theme code must satisfy — token schema, the CSS allowlist, the
  ThemeScene interface, the sandbox environment, and the budgets that get code rejected.
version: 1
status: active
priority: 10
owner: theming
---

# Theme engine contract

You are writing code that runs in players' browsers. It is validated before it ships, and
validation rejects the whole layer rather than patching it, so code that violates this
contract simply does not reach anyone.

## Three layers, three trust levels

| Layer | What it is | Where it runs |
|---|---|---|
| **Tokens** | JSON design tokens | Applied as CSS custom properties to the game UI |
| **CSS** | A scoped stylesheet | Main document, inside `[data-theme-scope]` |
| **Scene** | A TypeScript ES module | Sandboxed iframe, decorative only |

Tokens and CSS style the actual game. The scene never touches it — it draws behind the
grid with `pointer-events: none` and cannot reach the game's DOM.

## Tokens

Fill the schema exactly. Values are validated and out-of-range values are clamped or
rejected:

- **Palette.** Hex or bounded `hsl()`. Every foreground/background pair is checked against
  WCAG AA — 4.5:1 for text, 3:1 for UI boundaries. A palette that fails is auto-corrected
  by nudging lightness, and rejected if that does not fix it. Design *to* the contrast
  requirement rather than hoping; a beautiful unreadable grid is a failure.
- **Typography.** Font families come from a fixed allowlist of self-hosted faces. No
  `@font-face`, no remote URLs, no arbitrary family names. `gridFont` must be
  monospace-metric or the grid misaligns.
- **Shape and motion.** All numeric, all clamped. Easings are named, not arbitrary
  `cubic-bezier`.
- **`darkVariant`.** A partial palette override. Provide it; a theme that only works in
  one mode is half a theme.

## CSS

Scoped, allowlisted, and rejected whole-sheet on any violation.

**Allowed:** selectors within `[data-theme-scope="<id>"]`; a ~90-property allowlist
(color, background, border, transform, opacity, filter, box-shadow, animation,
transition); `@keyframes`; `@media` on `prefers-color-scheme`, `prefers-reduced-motion`,
and width; `var()`, `calc()`, gradients, transform functions.

**Rejected:** `url()` with any scheme other than `data:image/svg+xml` or `data:image/png`;
`@import`; `@font-face`; `position: fixed`; `z-index` above the ceiling; `content: url()`;
any selector targeting `[data-testid]`, `[data-grid]`, or `[data-game]`.

Most of a theme's motion should live here. CSS keyframes are GPU-composited, statically
checkable, and cheaper than anything the scene can do.

## Scene

```typescript
export interface ThemeScene {
  mount(ctx: ThemeSceneContext): void;
  onEvent(event: GameEvent): void;
  resize(w: number, h: number): void;
  setPaused(paused: boolean): void;
  unmount(): void;
}
export default function createScene(): ThemeScene;
```

`GameEvent` is the shared vocabulary across all games: `cell.filled`, `cell.cleared`,
`word.solved`, `word.error`, `puzzle.solved`, `streak.extended`, `idle`. Coordinates are
grid-relative and the frame is in exact registration with the grid, so a scene can react
under the word that was just solved without seeing the game's DOM.

### The environment

The frame is `sandbox="allow-scripts"` **without** `allow-same-origin`, so it has an opaque
origin: no parent DOM, no cookies, no storage, no credentialed requests. Its CSP sets
`connect-src 'none'` — there is no network. `fetch`, `XMLHttpRequest`, `WebSocket`, `eval`,
and `Function` are deleted from the global scope before your module is evaluated.

Write to that environment. Everything you need is local and passed in.

### Imports

Only these resolve:

| Module | Contents |
|---|---|
| `@scene/motion` | `animate`, `spring`, `stagger`, `timeline` |
| `@scene/draw` | Canvas 2D helpers: particles, gradients, noise fields, path tracing |
| `@scene/svg` | Safe declarative SVG builder — never `innerHTML` |
| `@scene/easing` | Named easing curves |
| `@scene/rng` | Seeded PRNG. Scenes must be deterministic per puzzle |

Any other import fails the static check and the scene is dropped.

### Budgets

- **48KB gzipped**, hard cap.
- **8ms average frame cost** across 60 frames, or the scene downgrades to static. **16ms**
  and it is unmounted entirely.
- Paused on tab blur, on `prefers-reduced-motion`, and on battery saver.

Design for the budget from the start. A beautiful scene that gets unmounted on a mid-tier
Android is worth less than a modest one that runs everywhere.

## Degradation

Every layer fails independently and the puzzle stays playable: scene fails → tokens + CSS;
CSS fails → tokens only; tokens fail → the default theme. Never write a theme where the
grid depends on the scene having mounted.

## Visual direction

Take the direction from the motif. Left to its own devices, generated design converges on
one house style — warm cream backgrounds, serif display type, terracotta accents — which
is handsome once and monotonous across fifty themes. Commit to the specific motif you were
given: a kelp forest is cold, deep, and green; a desert observatory is not.
