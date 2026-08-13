---
id: scene-particle-field
kind: pattern
applies_to:
  - theme_scene_generation
summary: >
  Use when the motif involves drifting or falling particulate — snow, dust, embers,
  bubbles, pollen, stars. Gives a frame-budget-safe pooled implementation and the density
  math that keeps it under 8ms.
triggers:
  motif_keywords:
    - snow
    - rain
    - dust
    - stars
    - bubbles
    - embers
    - pollen
    - drift
    - ash
    - plankton
version: 1
status: active
priority: 60
evidence: eval/theme-frame-budget@seed
owner: theming
---

# Particle fields within budget

Particles are the most requested theme effect and the most common way a scene gets
unmounted for blowing its frame budget. The failure is almost always the same three
mistakes.

## The three mistakes

**1. Allocating per frame.** `particles.push({...})` inside the RAF loop produces garbage
that the collector eventually stops for. Pool everything at mount and recycle.

**2. Scaling count with canvas area.** A desktop canvas is roughly nine times a phone's.
Density that looks right at 390px becomes 2,000 particles at 1440px. Scale sub-linearly
and cap.

**3. Per-particle `save()`/`restore()` and shadow blur.** Both are expensive. A field of
300 particles each with `shadowBlur` will not hit 60fps on a mid-tier Android, no matter
how simple the geometry.

## The density formula

```typescript
const AREA_REFERENCE = 390 * 844;        // a typical phone viewport
const BASE_COUNT = 90;                   // tuned for that reference
const MAX_COUNT = 240;                   // hard cap regardless of screen

const count = Math.min(
  MAX_COUNT,
  Math.round(BASE_COUNT * Math.sqrt((width * height) / AREA_REFERENCE)),
);
```

The square root is the point: a nine-times-larger canvas gets three times the particles,
which reads as the same visual density while costing a third of the naive count.

## Pooled implementation

Flat typed arrays rather than an array of objects — one allocation, cache-friendly
iteration, no per-particle indirection.

```typescript
const xs = new Float32Array(MAX_COUNT);
const ys = new Float32Array(MAX_COUNT);
const vxs = new Float32Array(MAX_COUNT);
const vys = new Float32Array(MAX_COUNT);
const rs = new Float32Array(MAX_COUNT);
const as = new Float32Array(MAX_COUNT);   // alpha

function seed(i: number, rng: () => number): void {
  xs[i] = rng() * width;
  ys[i] = rng() * height;
  vxs[i] = (rng() - 0.5) * 8;
  vys[i] = 6 + rng() * 18;
  rs[i] = 0.8 + rng() * 2.2;
  as[i] = 0.25 + rng() * 0.5;
}

function step(dt: number, count: number): void {
  const dts = dt / 1000;
  for (let i = 0; i < count; i++) {
    xs[i] += vxs[i] * dts;
    ys[i] += vys[i] * dts;
    if (ys[i] > height + 4) {          // recycle, never reallocate
      ys[i] = -4;
      xs[i] = Math.random() * width;
    }
  }
}
```

## Drawing cheaply

Group by alpha bucket so `globalAlpha` is set a handful of times rather than per particle:

```typescript
function draw(ctx: CanvasRenderingContext2D, count: number, colour: string): void {
  ctx.fillStyle = colour;
  for (let bucket = 0; bucket < 4; bucket++) {
    ctx.globalAlpha = 0.2 + bucket * 0.2;
    ctx.beginPath();
    for (let i = bucket; i < count; i += 4) {
      ctx.moveTo(xs[i] + rs[i], ys[i]);
      ctx.arc(xs[i], ys[i], rs[i], 0, Math.PI * 2);
    }
    ctx.fill();                        // one fill per bucket, not per particle
  }
  ctx.globalAlpha = 1;
}
```

One `beginPath`/`fill` per bucket instead of per particle is the difference between ~4ms
and ~14ms at 240 particles.

## Reacting to the game

Particles are a good surface for `word.solved` because the reaction can be local. Nudge
velocities near the solved word's cells rather than restarting the field:

```typescript
onEvent(event: GameEvent): void {
  if (event.type !== "word.solved") return;
  const impulse = event.isTheme ? 40 : 16;
  for (const cell of event.cells) {
    for (let i = 0; i < count; i++) {
      const dx = xs[i] - cell.x;
      const dy = ys[i] - cell.y;
      const d2 = dx * dx + dy * dy;
      if (d2 < 6400) {                    // 80px radius, squared — no sqrt
        vxs[i] += (dx / 80) * impulse;
        vys[i] += (dy / 80) * impulse;
      }
    }
  }
}
```

## Reduced motion

Draw one static frame with the particles distributed and stop. Do not simply skip
rendering — an empty canvas where a field was expected reads as a broken theme rather than
a considerate one.

## Budget check

At 240 particles with bucketed fills and no shadow, expect ~3–5ms per frame on a mid-tier
Android. If the motif needs glow, apply it once to the whole layer with a CSS `filter` on
the canvas element rather than per particle.
