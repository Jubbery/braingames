/*---
id: scene-canvas-minimal
kind: template
applies_to:
  - theme_scene_generation
summary: >
  Use as the starting skeleton for any canvas-based theme scene. A complete, correct,
  budget-safe ThemeScene with lifecycle, resize, pause, and event handling already wired.
triggers:
  always: true
version: 1
status: active
priority: 50
evidence: eval/theme-scene-validity@seed
owner: theming
---*/

import { createRng } from "@scene/rng";
import type { GameEvent, ThemeScene, ThemeSceneContext } from "@scene/types";

/**
 * Minimal canvas scene. Start here and replace `drawFrame` and `onEvent`.
 *
 * What this skeleton already gets right, and what you should not remove:
 *
 *  - Device pixel ratio is capped at 2. Uncapped DPR on a 3x phone triples
 *    fill cost for no visible gain and is the most common way a scene blows
 *    its frame budget.
 *  - The RAF loop stops when paused and when unmounted. A leaked loop keeps
 *    running after navigation and is invisible until the battery complains.
 *  - Randomness is seeded. Scenes must be deterministic per puzzle, or two
 *    players comparing screenshots see different things.
 *  - No allocation inside drawFrame. Particle state is pooled up front.
 */
export default function createScene(): ThemeScene {
  let ctx2d: CanvasRenderingContext2D | null = null;
  let sceneCtx: ThemeSceneContext | null = null;
  let raf = 0;
  let paused = false;
  let width = 0;
  let height = 0;
  let lastTs = 0;

  const rng = createRng("scene-canvas-minimal");

  function resize(w: number, h: number): void {
    if (!sceneCtx) return;
    const dpr = Math.min(globalThis.devicePixelRatio || 1, 2);
    width = w;
    height = h;
    sceneCtx.canvas.width = Math.floor(w * dpr);
    sceneCtx.canvas.height = Math.floor(h * dpr);
    sceneCtx.canvas.style.width = `${w}px`;
    sceneCtx.canvas.style.height = `${h}px`;
    ctx2d?.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawFrame(dt: number): void {
    if (!ctx2d || !sceneCtx) return;
    const { palette } = sceneCtx.tokens;

    ctx2d.clearRect(0, 0, width, height);
    ctx2d.fillStyle = palette.bg;
    ctx2d.fillRect(0, 0, width, height);

    // Replace with the motif. Keep per-frame work proportional to a fixed
    // budget rather than to canvas area — that is what keeps a scene inside
    // 8ms on a mid-tier phone as well as a laptop.
    void dt;
    void rng;
  }

  function loop(ts: number): void {
    if (paused) return;
    const dt = lastTs ? Math.min(ts - lastTs, 50) : 16;
    lastTs = ts;
    drawFrame(dt);
    raf = requestAnimationFrame(loop);
  }

  return {
    mount(context: ThemeSceneContext): void {
      sceneCtx = context;
      ctx2d = context.canvas.getContext("2d", { alpha: true });
      resize(context.canvas.clientWidth, context.canvas.clientHeight);

      // Respect the setting rather than animating and hoping. A static first
      // frame is the correct output under reduced motion, not an empty canvas.
      if (context.reducedMotion) {
        drawFrame(0);
        return;
      }
      raf = requestAnimationFrame(loop);
    },

    onEvent(event: GameEvent): void {
      switch (event.type) {
        case "word.solved":
          // Theme entries deserve a bigger reaction than ordinary fill.
          break;
        case "puzzle.solved":
          break;
        default:
          break;
      }
    },

    resize,

    setPaused(next: boolean): void {
      if (paused === next) return;
      paused = next;
      if (paused) {
        cancelAnimationFrame(raf);
        raf = 0;
      } else {
        lastTs = 0;
        raf = requestAnimationFrame(loop);
      }
    },

    unmount(): void {
      cancelAnimationFrame(raf);
      raf = 0;
      ctx2d = null;
      sceneCtx = null;
    },
  };
}
