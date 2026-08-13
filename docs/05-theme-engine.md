# 5. Theme Engine

## 5.1 The requirement, and the problem inside it

> "The theme should be graphically designed based on the theme, the agent would generate the code
> necessary to achieve the thematic style of the crossword."

So an AI writes CSS and TypeScript, and that code runs in players' browsers. Stated plainly:
**we are shipping model-generated code to production clients, daily, without a human in the
loop.** That's the feature and it's also a persistent XSS and supply-chain exposure. A generated
`fetch()` in a theme script would run with the player's session; a generated `innerHTML` with an
interpolated string is a stored XSS vector with an automated author.

The design below keeps the feature and closes the hole, by splitting the theme into two layers
with very different trust levels.

## 5.2 Two layers

| | Layer A — **Theme Tokens & Styles** | Layer B — **Theme Scene** |
|---|---|---|
| What it is | Declarative design tokens + a scoped CSS module | An ES module rendering an animated decorative layer |
| Touches | The grid, clues, chrome — the actual game UI | Background, transitions, celebration effects only |
| Trust | Validated, then trusted | Never trusted |
| Execution | Main document | Sandboxed `iframe` |
| Can it break the game? | Visually, in bounded ways | No — it is not in the game's DOM |
| Model output | JSON + CSS | TypeScript |

Layer A is where 80% of the perceived theming lives, and it carries no script. Layer B is where
the delight lives — drifting particles, a wave that crests when you solve a long entry, confetti
in the theme's palette — and it is completely isolated from anything worth attacking.

## 5.3 Layer A — Tokens and styles

### Tokens

A strict Pydantic/Zod schema. The model fills it; nothing arbitrary gets through.

```typescript
interface ThemeTokens {
  id: string;
  name: string;
  motif: string;

  palette: {
    // every value validated as a hex or a bounded hsl()
    bg: string; surface: string; surfaceAlt: string;
    gridLine: string; cellEmpty: string; cellFilled: string;
    cellSelected: string; cellHighlight: string; cellError: string;
    cellThemeAccent: string;
    text: string; textMuted: string; textOnAccent: string;
    accent: string; accentAlt: string;
  };

  typography: {
    // font family from an allowlist of self-hosted fonts. No @font-face,
    // no remote URLs, no arbitrary family names.
    displayFont: AllowedFont;
    gridFont: AllowedFont;          // must be monospace-metric for grid alignment
    clueFont: AllowedFont;
    gridLetterWeight: 400 | 500 | 600 | 700;
    clueScale: number;              // clamped 0.9..1.15
  };

  shape: {
    cellRadius: number;             // clamped 0..8 px
    gridGap: number;                // clamped 0..3 px
    surfaceRadius: number;          // clamped 0..24 px
    borderWidth: number;            // clamped 0..3 px
  };

  motion: {
    cellFillDuration: number;       // clamped 60..400 ms
    cellFillEasing: AllowedEasing;  // named easings only, no arbitrary cubic-bezier
    wordSolveEffect: 'pulse' | 'sweep' | 'glow' | 'ripple' | 'none';
    puzzleSolveEffect: 'confetti' | 'bloom' | 'cascade' | 'shimmer' | 'none';
  };

  darkVariant: Partial<ThemeTokens['palette']>;
}
```

Tokens become CSS custom properties on a wrapper element. MUI reads them through a generated
theme object, so the entire component tree — buttons, dialogs, the clue list — themes coherently
without any component knowing a theme engine exists:

```tsx
const muiTheme = createTheme({
  palette: {
    mode: prefersDark ? 'dark' : 'light',
    primary:    { main: tokens.palette.accent },
    background: { default: tokens.palette.bg, paper: tokens.palette.surface },
    text:       { primary: tokens.palette.text, secondary: tokens.palette.textMuted },
  },
  shape: { borderRadius: tokens.shape.surfaceRadius },
  typography: { fontFamily: FONT_STACKS[tokens.typography.clueFont] },
});
```

**Contrast is validated, not hoped for.** Every foreground/background pair in the token set is
checked against WCAG AA (4.5:1 for text, 3:1 for UI boundaries) at generation time. A palette
that fails is auto-corrected by nudging lightness, and if it still fails, rejected. A theme that
makes the grid unreadable is worse than no theme, and models will absolutely produce beautiful
low-contrast palettes if nothing stops them.

### Scoped CSS

The model may also emit a CSS module for effects that tokens can't express — a subtle grain
texture, a themed gradient on the header, a custom keyframe for cell entry.

This CSS goes through a **PostCSS AST allowlist** before it is ever served:

```
ALLOWED
  selectors:   only within [data-theme-scope="<id>"]; no :root, no html, no body
  properties:  a ~90-entry allowlist (color, background, border*, transform,
               opacity, filter, box-shadow, animation*, transition*, ...)
  at-rules:    @keyframes, @media (prefers-color-scheme | prefers-reduced-motion | width)
  functions:   var(), calc(), rgb(), hsl(), linear-gradient(), radial-gradient(),
               cubic-bezier(), and the transform functions

REJECTED — the whole stylesheet, not just the rule
  url() with any scheme other than data:image/svg+xml or data:image/png
  @import, @font-face, behavior, expression(), -moz-binding
  position: fixed          (can cover the whole viewport)
  z-index above a ceiling  (can cover the game)
  content: with url()
  anything targeting [data-testid], [data-grid], or [data-game] internals
```

A rejected stylesheet drops the theme to tokens-only. The puzzle is still fully themed and fully
playable; it just loses the extra texture. This is the right failure mode: degrade the decoration,
never the game.

## 5.4 Layer B — The Theme Scene

### Why the sandbox

Layer B is generated TypeScript. There is no static analysis that reliably proves generated
script is safe, so we stop trying to prove it and remove its capabilities instead.

The scene runs in an `<iframe sandbox="allow-scripts">` with **no `allow-same-origin`**. That
single omission is what does the work: the frame gets an opaque origin, so it cannot read the
parent DOM, cannot touch cookies or `localStorage`, and cannot make credentialed requests. The
frame's document carries a CSP that blocks network egress entirely:

```
default-src 'none';
script-src 'unsafe-inline' blob:;
style-src 'unsafe-inline';
img-src data: blob:;
connect-src 'none';
frame-src 'none';
```

A malicious or merely buggy theme can, at absolute worst, draw something ugly or spin the CPU in
its own frame. It cannot read a session, exfiltrate a profile, or reach the player's account.

### The contract

```typescript
export interface ThemeSceneContext {
  canvas: HTMLCanvasElement;        // the scene's own canvas, sized to the frame
  root: HTMLElement;                // the scene's own root, for SVG/DOM effects
  tokens: ThemeTokens;              // read-only
  reducedMotion: boolean;
  dark: boolean;
}

export interface ThemeScene {
  mount(ctx: ThemeSceneContext): void;
  onEvent(event: GameEvent): void;
  resize(w: number, h: number): void;
  setPaused(paused: boolean): void;
  unmount(): void;
}

export type GameEvent =
  | { type: 'cell.filled';   row: number; col: number; letter: string }
  | { type: 'cell.cleared';  row: number; col: number }
  | { type: 'word.solved';   entryId: string; isTheme: boolean; cells: Cell[] }
  | { type: 'word.error';    entryId: string }
  | { type: 'puzzle.solved'; elapsedMs: number; assisted: boolean }
  | { type: 'streak.extended'; days: number }
  | { type: 'idle' };

export default function createScene(): ThemeScene;
```

The parent posts `GameEvent`s in; the frame posts nothing back except `{ ready }` and
`{ error }`. Coordinates in events are grid-relative and the frame is overlaid in exact
registration with the grid, so a scene can make a wave crest under the word you just solved
without ever seeing the game's DOM.

### What the model may import

Nothing arbitrary. The frame preloads a fixed runtime and the generated module resolves imports
against it only:

| Module | Purpose |
|---|---|
| `@scene/motion` | `animate`, `spring`, `stagger`, `timeline` — a thin wrapper over the Web Animations API and `motion`'s DOM primitives |
| `@scene/draw` | Canvas 2D helpers: particles, gradients, noise fields, path tracing |
| `@scene/svg` | Declarative SVG element builder (safe construction — no `innerHTML`) |
| `@scene/easing` | Named easing curves |
| `@scene/rng` | Seeded PRNG — themes must be deterministic per puzzle |

`fetch`, `XMLHttpRequest`, `WebSocket`, `eval`, `Function`, `importScripts`, and
`document.write` are deleted from the frame's global scope before the module is evaluated. The
CSP is the real boundary; this is a second layer so that a broken scene fails loudly at
development time rather than quietly at runtime.

### Why this library set

The user asked what an AI agent can use to build animated themes from HTML/CSS/TypeScript. The
short answer is: **things an LLM can write fluently as source text.**

- **CSS `@keyframes` + custom properties** — the highest-quality-per-token output a model
  produces. It has seen enormous amounts of it, it's declarative, it's GPU-composited, and it's
  statically checkable. Most of the theme's motion should live here.
- **Motion / Web Animations API** — imperative animation where CSS can't express the
  choreography (staggered reveals keyed to solve order, spring physics on the celebration).
  Models write `animate(el, { ... }, { ... })` accurately.
- **Inline SVG + Canvas 2D** — procedural motifs. A model writing "draw drifting kelp fronds in
  the accent color" produces good SVG paths and good canvas code.

Rejected, and worth saying why: **Lottie** and **Rive** are excellent runtimes and both use
binary/editor-authored formats — a model can't author them, so they'd need a human designer per
theme, which defeats the point. **Three.js** is too heavy for a decorative layer on a crossword
and too easy to make janky. **GSAP** is superb but its licensing and bundle size don't earn their
place next to the Web Animations API here.

### Static checks before it ever runs

Even with the sandbox, the generated module is parsed and checked — cheap, catches the boring
failures before they reach a player:

```
Parse with the TypeScript compiler API. Reject on:
  - any import not in the allowlist
  - identifiers: eval, Function, importScripts, fetch, XMLHttpRequest, WebSocket,
    postMessage (outside the provided helper), localStorage, document.cookie,
    window.parent, window.top, window.opener
  - innerHTML / outerHTML / insertAdjacentHTML assignment
  - while(true) / for(;;) without a bounded exit
  - module size over 48KB
```

### Runtime budget

The scene is decoration and must behave like it:

- Its `requestAnimationFrame` loop is instrumented. Over 8ms average frame cost across 60 frames,
  the scene is downgraded to static; over 16ms it's unmounted entirely and the theme falls back
  to tokens-only.
- Paused on tab blur, on `prefers-reduced-motion`, and on battery saver where detectable.
- Never mounted on devices below a hardware-concurrency threshold.
- Hard-capped at 48KB gzipped per scene.

## 5.5 Generation

One call per **theme**, not per puzzle — themes are cached and reused across cohorts sharing a
motif, so this is a small fraction of the cost model.

```python
class GeneratedTheme(BaseModel):
    tokens: ThemeTokens
    css: str = Field(max_length=12000)
    scene_ts: str = Field(max_length=48000)
    rationale: str

response = client.messages.create(
    model="claude-opus-5",
    max_tokens=32000,
    thinking={"type": "adaptive"},
    output_config={
        "effort": "high",
        "format": {"type": "json_schema", "schema": GeneratedTheme.model_json_schema()},
    },
    system=[
        {"type": "text", "text": THEME_ENGINE_SPEC,          # tokens schema, CSS allowlist,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}}, # scene API, examples — ~14k tokens
    ],
    messages=[{"role": "user", "content": theme_design_request(concept, motif)}],
)
```

The system prompt is the full engine spec — schema, allowlists, the `ThemeScene` interface, and
three complete worked examples. It's large and completely stable, so it sits behind a cache
breakpoint and reads at ~0.1× on every subsequent theme.

Two prompt notes, both learned the hard way with current models:

- **Give a concrete visual direction or ask for options.** Left open-ended, models settle into a
  consistent house style — warm cream backgrounds, serif display type, terracotta accents. It's
  handsome and it will make every theme look the same. Either pin the palette family from the
  motif, or have the model propose four distinct directions and select programmatically.
- **State the boundary as context, not prohibition.** "The scene runs in a sandboxed frame with
  no network and no parent access; write to that environment" produces better code than a list of
  banned identifiers.

### Validation pipeline

```
tokens  →  schema validate  →  contrast check (WCAG AA)  →  auto-correct → recheck
css     →  PostCSS AST allowlist  →  reject whole sheet on any violation
scene   →  tsc parse + identifier scan  →  bundle with esbuild  →  size check
        →  headless smoke test (Playwright):
             mount, replay a scripted 60-event solve, capture frames,
             assert no console errors, assert frame budget, screenshot diff
        →  store bundle in R2, content-addressed
```

Any failure downgrades gracefully: scene fails → tokens + CSS. CSS fails → tokens only. Tokens
fail → the default theme. There is no path from a bad generation to a broken puzzle.

## 5.6 Delivery

```
theme_bundle/
  <content-hash>/
    tokens.json          ~2KB    fetched with the puzzle metadata
    theme.css            ~4KB    injected as a scoped <style>
    scene.js             ~20KB   fetched by the iframe, lazily, after first paint
    preview.png          ~30KB   for the archive and theme picker
```

Immutable and content-addressed, so `Cache-Control: public, max-age=31536000, immutable`. The
scene is loaded lazily and only after the grid is interactive — theming must never sit on the
critical path to a playable puzzle.

## 5.7 Player controls

Settings → Appearance:

- **Puzzle themes**: Full (tokens + CSS + scene) · Colors only · Off (system theme)
- **Animations**: Full · Reduced · Off — `prefers-reduced-motion` is respected as the default
  and this control overrides it in either direction
- **Contrast**: Standard · High — High forces a validated high-contrast palette regardless of
  the generated one
- **Font size**: three steps, independent of the theme's `clueScale`

Accessibility is not a mode the theme system can override. High contrast and reduced motion win
over every generated token, and there is no theme-authored path to disabling them.
