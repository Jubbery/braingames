"""The fill solver: backtracking search with constraint propagation.

No model involvement. Filling a grid is a constraint-satisfaction problem, and
the reason docs/04 §4.1 splits the pipeline here is that this is precisely the
part language models are unreliable at and a solver is exact at.

Three things make it fast enough to run inside a nightly job:

* **Bitset domains.** A slot's candidate set is a Python ``int``; propagating a
  letter to a crossing entry is one ``&``. Popcount for the MRV heuristic is
  ``int.bit_count()``.
* **Score-ordered words.** Entries are sorted by score descending within each
  length, so walking a domain from its lowest set bit yields the best fill
  first. Good ordering comes free from the bit order.
* **A trail.** Undo is a list of (slot, previous domain) pairs rather than a
  copy of the whole state.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from braingames_core import obs

from .lexicon import OBSCURE_THRESHOLD, LexiconEntry, LexiconIndex, iter_bits
from .models import Fill, GridPattern, Slot

log = obs.get_logger(__name__)

#: Quality gates per tier, from docs/04 §4.5. Themed grids are held to a higher
#: mean because they have fewer degrees of freedom left after the theme.
MEAN_SCORE_FLOOR: dict[int, int] = {1: 55, 2: 50, 3: 45}
ABSOLUTE_SCORE_FLOOR = 25

#: How many obscure entries a tier tolerates. Set to what a solver would accept,
#: not to what the current word list can achieve — see the note in
#: ``evaluate``. A Monday should have almost none.
MAX_OBSCURE_ENTRIES: dict[int, int] = {1: 3, 2: 6, 3: 10}

#: Floor on how few candidates a length may be left with before its score floor
#: is relaxed. Deliberately tied to ``max_candidates`` rather than set high:
#: the net exists to stop a length going empty, and setting it high would quietly
#: cancel the raised floor that ``fill_with_retries`` uses to improve quality.
#: Below what the search would actually examine, the floor is discarding options
#: for no gain; above it, the floor is doing its job and is left alone.
MIN_DOMAIN = 8


@dataclass
class FillConfig:
    tier: int = 2
    #: How many candidates to try per slot before backtracking. Unbounded search
    #: is exponential and rarely finds anything the top few dozen would not.
    max_candidates: int = 40
    #: Total wall-clock budget across every restart.
    time_budget_s: float = 30.0
    node_limit: int = 400_000
    #: Words scoring below this are excluded outright. Raised on retry when the
    #: fill succeeds but fails its quality gates.
    min_score: int = ABSOLUTE_SCORE_FLOOR
    seed: int | None = None

    #: Restart policy. Measured behaviour is sharply bimodal: a grid this
    #: lexicon can fill is usually filled in well under a second, and one that
    #: thrashes still thrashes twenty seconds later — 1.5M backtracks deep in a
    #: subtree the search should have abandoned. That is the classic
    #: heavy-tailed runtime a restart fixes and more search does not. Each
    #: attempt gets a slice of the budget; later attempts jitter the candidate
    #: order so they explore a different part of the tree instead of repeating
    #: the first attempt exactly.
    restarts: int = 6
    #: How far a word may drift from its score rank when ordering candidates.
    #: Zero on the first attempt: best-first is the fast path.
    jitter: float = 0.0


@dataclass
class FillQuality:
    mean_score: float
    min_score: int
    obscure_entries: list[str] = field(default_factory=list)
    obscure_crossings: list[tuple[str, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return not self.failures


@dataclass
class FillResult:
    fill: Fill | None
    quality: FillQuality | None
    reason: str
    nodes: int = 0
    backtracks: int = 0
    attempts: int = 0
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.fill is not None and self.fill.is_complete


class _Search:
    """One search over one grid. Holds the mutable state the recursion needs."""

    def __init__(
        self,
        pattern: GridPattern,
        index: LexiconIndex,
        theme: dict[str, str],
        config: FillConfig,
    ) -> None:
        self.pattern = pattern
        self.index = index
        self.config = config
        self.slots: tuple[Slot, ...] = pattern.slots
        self.rng = random.Random(config.seed)

        # cell -> [(slot index, position within that slot), ...]
        self.crossings: dict[int, list[tuple[int, int]]] = {}
        for si, slot in enumerate(self.slots):
            for pos, cell in enumerate(slot.cells):
                self.crossings.setdefault(cell, []).append((si, pos))

        self.assigned: dict[int, str] = {}
        self.used: set[str] = set()
        self.domains: list[int] = [self._initial_domain(s.length) for s in self.slots]

        self.nodes = 0
        self.backtracks = 0
        self.deadline = time.monotonic() + config.time_budget_s
        self.timed_out = False
        self.impossible: str | None = None

        self._seed_theme(theme)

    # ------------------------------------------------------------------

    def _initial_domain(self, length: int) -> int:
        """All words of this length scoring at or above the floor.

        Words are score-sorted, so the acceptable set is always a prefix and the
        mask is a single shift — no filtering pass.

        **The floor is relaxed for any length it would starve.** A scoring
        change once left 15-letter slots with 44 candidates out of 8,847; MRV
        correctly picked those slots first and the search died at the root, and
        the failure surfaced as "exhausted after 41 nodes" with nothing pointing
        at the lexicon. A score floor is a search *preference* — the quality
        gates in ``evaluate`` are the actual contract, and they run on the
        finished fill either way. So a floor is never allowed to make a length
        unfillable; it gets widened just enough to keep searching, and says so.
        """
        scores = self.index.scores.get(length)
        if not scores:
            return 0
        cutoff = len(scores)
        for i, score in enumerate(scores):
            if score < self.config.min_score:
                cutoff = i
                break

        needed = max(MIN_DOMAIN, self.config.max_candidates)
        if cutoff < needed and len(scores) > cutoff:
            widened = min(needed, len(scores))
            log.warning(
                "lexicon_starved_length",
                extra={
                    "length": length,
                    "min_score": self.config.min_score,
                    "candidates_at_floor": cutoff,
                    "widened_to": widened,
                    "lowest_score_admitted": scores[widened - 1],
                },
            )
            cutoff = widened

        return (1 << cutoff) - 1

    def _seed_theme(self, theme: dict[str, str]) -> None:
        """Pin theme entries. They are given, not searched.

        Theme answers are frequently absent from the lexicon — that is the
        point of a theme — so they are applied as letters directly rather than
        looked up.
        """
        by_id = {s.id: (i, s) for i, s in enumerate(self.slots)}
        for slot_id, answer in theme.items():
            if slot_id not in by_id:
                self.impossible = f"Theme slot {slot_id!r} is not in this grid"
                return
            si, slot = by_id[slot_id]
            if len(answer) != slot.length:
                self.impossible = (
                    f"Theme answer {answer!r} is {len(answer)} letters but slot "
                    f"{slot_id} takes {slot.length}"
                )
                return
            if not self._assign(si, answer, trail=None):
                self.impossible = f"Theme answer {answer!r} leaves a crossing with no candidates"
                return

    # ------------------------------------------------------------------

    def _assign(self, si: int, word: str, trail: list[tuple[int, int]] | None) -> bool:
        """Place a word and propagate its letters. False if it kills a crossing."""
        slot = self.slots[si]
        self.assigned[si] = word
        self.used.add(word)

        for pos, cell in enumerate(slot.cells):
            letter = word[pos]
            for other_si, other_pos in self.crossings[cell]:
                if other_si == si or other_si in self.assigned:
                    continue
                other = self.slots[other_si]
                mask = self.index.masks[other.length][other_pos][ord(letter) - 65]
                narrowed = self.domains[other_si] & mask
                if narrowed == self.domains[other_si]:
                    continue
                if trail is not None:
                    trail.append((other_si, self.domains[other_si]))
                self.domains[other_si] = narrowed
                if narrowed == 0:
                    return False
        return True

    def _undo(self, si: int, word: str, trail: list[tuple[int, int]]) -> None:
        for other_si, previous in reversed(trail):
            self.domains[other_si] = previous
        del self.assigned[si]
        self.used.discard(word)

    def _select(self) -> int | None:
        """Most-constrained slot first, ties broken by most unassigned crossings.

        MRV is what keeps the search from wandering: filling the slot with the
        fewest options first surfaces dead ends immediately instead of after
        another ten assignments.
        """
        best: int | None = None
        best_key: tuple[int, int] | None = None
        for si in range(len(self.slots)):
            if si in self.assigned:
                continue
            size = self.domains[si].bit_count()
            if size == 0:
                return si  # a dead end; let the caller fail fast
            degree = sum(
                1
                for cell in self.slots[si].cells
                for other_si, _ in self.crossings[cell]
                if other_si != si and other_si not in self.assigned
            )
            key = (size, -degree)
            if best_key is None or key < best_key:
                best, best_key = si, key
        return best

    def _candidates(self, si: int) -> list[str]:
        """Best-scoring candidates first, optionally jittered.

        Bit order is score order, so the plain walk is already best-first. The
        jitter lets a restart take a different path through the same tree while
        still preferring good fill — a word can drift a few places, not to the
        end of the list.
        """
        slot = self.slots[si]
        words = self.index.words[slot.length]
        out: list[str] = []
        for i in iter_bits(self.domains[si]):
            word = words[i]
            if word in self.used:
                continue
            out.append(word)
            if len(out) >= self.config.max_candidates:
                break

        jitter = self.config.jitter
        if jitter:
            random_ = self.rng.random
            ranked = sorted(
                ((rank + random_() * jitter, word) for rank, word in enumerate(out)),
            )
            out = [word for _, word in ranked]
        return out

    def run(self) -> bool:
        if self.impossible:
            return False
        return self._recurse()

    def _recurse(self) -> bool:
        if len(self.assigned) == len(self.slots):
            return True

        self.nodes += 1
        if self.nodes % 512 == 0 and time.monotonic() > self.deadline:
            self.timed_out = True
            return False

        si = self._select()
        if si is None:
            return True
        if self.domains[si] == 0:
            return False

        for word in self._candidates(si):
            trail: list[tuple[int, int]] = []
            if self._assign(si, word, trail) and self._recurse():
                return True
            self._undo(si, word, trail)
            self.backtracks += 1
            if self.timed_out or self.nodes > self.config.node_limit:
                return False

        return False


def evaluate(
    fill: Fill,
    index: LexiconIndex,
    *,
    tier: int,
    theme_slots: set[str] | None = None,
) -> FillQuality:
    """Score a completed fill against the quality gates.

    Theme entries are excluded from scoring: they were chosen deliberately and
    are usually absent from the lexicon, so judging them on fill quality would
    penalise exactly the entries the puzzle is about.

    **These gates are calibrated to what a solver will accept, and the bundled
    word list does not currently meet them.** A measured 15x15 came back with 35
    of its 72 non-theme entries attested nowhere but the dictionary. That is a
    true statement about the fill and the gate should say so, so the thresholds
    are not loosened to let it through. A gate tuned until the current data
    passes measures the data, not the puzzle.
    """
    theme_slots = theme_slots or set()
    pattern = fill.pattern

    scored: list[tuple[str, LexiconEntry]] = []
    unknown: list[str] = []
    for slot_id, word in fill.answers.items():
        if slot_id in theme_slots:
            continue
        located = index.index_of.get(word)
        if located is None:
            unknown.append(word)
            continue
        length, i = located
        scored.append((slot_id, index.entry(length, i)))

    failures: list[str] = []
    if unknown:
        failures.append(f"{len(unknown)} entries not in the lexicon: {', '.join(unknown[:5])}")

    scores = [e.score for _, e in scored]
    mean = round(sum(scores) / len(scores), 2) if scores else 0.0
    worst = min(scores) if scores else 0

    floor = MEAN_SCORE_FLOOR.get(tier, 50)
    if mean < floor:
        failures.append(f"mean score {mean} below the tier-{tier} floor of {floor}")
    if worst < ABSOLUTE_SCORE_FLOOR:
        failures.append(
            f"an entry scores {worst}, below the absolute floor of {ABSOLUTE_SCORE_FLOOR}"
        )

    obscure = {slot_id: entry for slot_id, entry in scored if entry.obscurity > OBSCURE_THRESHOLD}
    obscure_limit = MAX_OBSCURE_ENTRIES.get(tier, 5)
    if len(obscure) > obscure_limit:
        worst_first = sorted(obscure.values(), key=lambda e: -e.obscurity)
        failures.append(
            f"{len(obscure)} obscure entries (tier-{tier} limit {obscure_limit}): "
            + ", ".join(e.word for e in worst_first[:5])
        )

    # The single most important fairness rule: a solver must always be able to
    # infer a letter from at least one of the two entries crossing it.
    bad_crossings: list[tuple[str, str]] = []
    for cell in pattern.white_cells:
        ids = [pattern.slots[si].id for si in pattern.slots_by_cell[cell]]
        if len(ids) == 2 and ids[0] in obscure and ids[1] in obscure:
            pair = (fill.answers[ids[0]], fill.answers[ids[1]])
            if pair not in bad_crossings:
                bad_crossings.append(pair)
    if bad_crossings:
        failures.append(
            f"{len(bad_crossings)} obscure-on-obscure crossing(s): "
            + ", ".join(f"{a}x{b}" for a, b in bad_crossings[:3])
        )

    return FillQuality(
        mean_score=mean,
        min_score=worst,
        obscure_entries=sorted(e.word for e in obscure.values()),
        obscure_crossings=bad_crossings,
        failures=failures,
    )


def fill_grid(
    pattern: GridPattern,
    index: LexiconIndex,
    *,
    theme: dict[str, str] | None = None,
    config: FillConfig | None = None,
) -> FillResult:
    """Fill one grid, restarting a stuck search. See ``fill_with_retries``
    for the quality-gate loop above this one."""
    config = config or FillConfig()
    theme = theme or {}
    started = time.monotonic()

    with obs.span(
        "puzzle.fill",
        size=pattern.size,
        word_count=pattern.word_count,
        tier=config.tier,
        min_score=config.min_score,
    ):
        attempts = max(1, config.restarts)
        slice_s = config.time_budget_s / attempts
        total_nodes = 0
        total_backtracks = 0
        last_reason = "exhausted"

        for attempt in range(attempts):
            remaining = config.time_budget_s - (time.monotonic() - started)
            if remaining <= 0:
                last_reason = "timeout"
                break

            search = _Search(
                pattern,
                index,
                theme,
                _attempt_config(config, attempt, min(slice_s, remaining)),
            )
            solved = search.run()
            total_nodes += search.nodes
            total_backtracks += search.backtracks

            if solved:
                answers = {search.slots[si].id: word for si, word in search.assigned.items()}
                fill = Fill(pattern=pattern, answers=answers)
                quality = evaluate(fill, index, tier=config.tier, theme_slots=set(theme))
                elapsed = int((time.monotonic() - started) * 1000)

                obs.set_span_attributes(
                    nodes=total_nodes,
                    backtracks=total_backtracks,
                    attempts=attempt + 1,
                    mean_score=quality.mean_score,
                )
                log.info(
                    "fill_solved",
                    extra={
                        "nodes": total_nodes,
                        "backtracks": total_backtracks,
                        "attempts": attempt + 1,
                        "elapsed_ms": elapsed,
                        "mean_score": quality.mean_score,
                        "gates_pass": quality.passes,
                    },
                )
                return FillResult(
                    fill=fill,
                    quality=quality,
                    reason="solved",
                    nodes=total_nodes,
                    backtracks=total_backtracks,
                    attempts=attempt + 1,
                    elapsed_ms=elapsed,
                )

            # A bad theme or an unfillable geometry does not improve with a
            # reshuffle — only a genuinely stuck search is worth restarting.
            if search.impossible:
                elapsed = int((time.monotonic() - started) * 1000)
                log.info(
                    "fill_failed",
                    extra={"reason": "impossible", "detail": search.impossible},
                )
                return FillResult(
                    fill=None,
                    quality=None,
                    reason=search.impossible,
                    nodes=total_nodes,
                    backtracks=total_backtracks,
                    attempts=attempt + 1,
                    elapsed_ms=elapsed,
                )

            last_reason = (
                "timeout"
                if search.timed_out
                else "node_limit"
                if search.nodes > search.config.node_limit
                else "exhausted"
            )

        elapsed = int((time.monotonic() - started) * 1000)
        log.info(
            "fill_failed",
            extra={
                "reason": last_reason,
                "attempts": attempts,
                "nodes": total_nodes,
                "backtracks": total_backtracks,
                "elapsed_ms": elapsed,
            },
        )
        return FillResult(
            fill=None,
            quality=None,
            reason=last_reason,
            nodes=total_nodes,
            backtracks=total_backtracks,
            attempts=attempts,
            elapsed_ms=elapsed,
        )


def _attempt_config(config: FillConfig, attempt: int, budget_s: float) -> FillConfig:
    """Config for one restart: first attempt deterministic, later ones diversified."""
    return FillConfig(
        **{
            **config.__dict__,
            "time_budget_s": budget_s,
            "node_limit": max(1, config.node_limit // max(1, config.restarts)),
            "jitter": 0.0 if attempt == 0 else 3.0 + attempt,
            "seed": None if config.seed is None else config.seed + attempt * 7919,
        }
    )


def fill_with_retries(
    pattern: GridPattern,
    index: LexiconIndex,
    *,
    theme: dict[str, str] | None = None,
    config: FillConfig | None = None,
    score_steps: tuple[int, ...] = (0, 10, 20),
) -> FillResult:
    """Fill, and on a gate failure retry with a raised score floor.

    A fill that completes but scores badly means the search settled for junk it
    was allowed to use. Raising the floor removes that option rather than
    hoping a reshuffle avoids it (docs/04 §4.5).

    When no floor produces a fill that passes, the **best completed** fill is
    returned with its failing quality attached — not the last attempt, which is
    usually the one whose floor was so high nothing could be filled at all. A
    graded near-miss is something the caller can route to repair or to review; a
    bare "exhausted" is not, and throwing away a finished grid to report one
    would be losing the only useful thing the search produced.
    """
    config = config or FillConfig()
    last: FillResult | None = None
    best: FillResult | None = None

    for bump in score_steps:
        attempt = FillConfig(**{**config.__dict__, "min_score": config.min_score + bump})
        result = fill_grid(pattern, index, theme=theme, config=attempt)
        last = result
        if result.ok and result.quality is not None:
            if result.quality.passes:
                return result
            if best is None or _closer(result, best):
                best = result
        if not result.ok and result.reason.startswith(("impossible", "Theme")):
            break  # a raised floor cannot fix an unplaceable theme

    return best or last or FillResult(fill=None, quality=None, reason="not attempted")


def _closer(candidate: FillResult, incumbent: FillResult) -> bool:
    """Which near-miss is nearer? Fewest failing gates, then highest mean."""
    a, b = candidate.quality, incumbent.quality
    if a is None or b is None:
        return a is not None
    return (len(a.failures), -a.mean_score) < (len(b.failures), -b.mean_score)


__all__ = [
    "ABSOLUTE_SCORE_FLOOR",
    "MAX_OBSCURE_ENTRIES",
    "MEAN_SCORE_FLOOR",
    "MIN_DOMAIN",
    "OBSCURE_THRESHOLD",
    "FillConfig",
    "FillQuality",
    "FillResult",
    "evaluate",
    "fill_grid",
    "fill_with_retries",
]
