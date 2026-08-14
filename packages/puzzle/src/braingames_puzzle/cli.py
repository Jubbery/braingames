"""``bg puzzle`` — build the construction data, fill grids, inspect the result.

Registered onto the existing ``bg`` app rather than shipping a second entry
point: one CLI is one thing to learn and one place for shell completion.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from braingames_core.config import settings

from . import templates as tpl
from .lexicon import (
    SCORE_FAMILIAR,
    SCORE_VALID,
    LexiconIndex,
    Source,
    ingest,
    load,
    save,
)
from .models import GridPattern, Puzzle
from .qa import Severity, run_checks
from .solver import FillConfig, fill_with_retries

puzzle_app = typer.Typer(no_args_is_help=True, help="Crossword construction")
lexicon_app = typer.Typer(no_args_is_help=True, help="Word list")
template_app = typer.Typer(no_args_is_help=True, help="Grid templates")
puzzle_app.add_typer(lexicon_app, name="lexicon")
puzzle_app.add_typer(template_app, name="templates")

console = Console()
err = Console(stderr=True)

#: How the bundled sources map onto score bands. A ranked source scores by
#: position; the others carry one flat level of attestation.
DEFAULT_SOURCES: tuple[tuple[str, str, bool, int], ...] = (
    ("words_alpha", "words_alpha.txt", False, SCORE_VALID),
    ("popular", "popular.txt", False, SCORE_FAMILIAR),
    ("google-10k", "google-10000-english.txt", True, 0),
)


def _index() -> LexiconIndex:
    path = settings().lexicon_path
    try:
        entries = load(path)
    except (FileNotFoundError, ValueError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    return LexiconIndex(entries)


# ----------------------------------------------------------------------
# bg puzzle lexicon
# ----------------------------------------------------------------------


@lexicon_app.command("build")
def lexicon_build(
    source_dir: Annotated[Path | None, typer.Option(help="Directory of raw word lists")] = None,
    out: Annotated[Path | None, typer.Option(help="Where to write the lexicon")] = None,
) -> None:
    """Ingest the raw word lists into one scored lexicon."""
    source_dir = source_dir or settings().wordlist_dir
    out = out or settings().lexicon_path

    sources = []
    for name, filename, ranked, flat in DEFAULT_SOURCES:
        path = source_dir / filename
        if not path.exists():
            err.print(f"[yellow]skipping {name}: {path} not found[/yellow]")
            continue
        sources.append(Source(name, path, ranked=ranked, flat_score=flat or SCORE_VALID))

    if not sources:
        err.print(f"[red]No word lists in {source_dir}[/red]")
        raise typer.Exit(code=1)

    entries = ingest(sources)
    count = save(entries, out)
    console.print(f"Wrote [bold]{count}[/bold] entries to {out}")


@lexicon_app.command("stats")
def lexicon_stats() -> None:
    """Score and obscurity distribution by entry length.

    Worth reading after any scoring change: a length whose candidate count
    collapses is the shape of a solver that will fail without saying why.
    """
    index = _index()
    table = Table(title=f"Lexicon — {index.size} entries")
    table.add_column("len", justify="right")
    table.add_column("words", justify="right")
    table.add_column("familiar or better", justify="right")
    table.add_column("obscure", justify="right")
    table.add_column("mean score", justify="right")

    for length in index.lengths:
        entries = index.entries[length]
        familiar = sum(1 for e in entries if e.obscurity <= 0.45)
        obscure = sum(1 for e in entries if e.obscurity > 0.7)
        mean = statistics.mean(e.score for e in entries)
        table.add_row(
            str(length),
            str(len(entries)),
            str(familiar),
            str(obscure),
            f"{mean:.1f}",
        )
    console.print(table)


@lexicon_app.command("lookup")
def lexicon_lookup(word: str) -> None:
    """Show one entry's score and obscurity."""
    index = _index()
    located = index.index_of.get(word.upper())
    if located is None:
        err.print(f"[yellow]{word.upper()} is not in the lexicon[/yellow]")
        raise typer.Exit(code=1)
    entry = index.entry(*located)
    console.print(
        f"[bold]{entry.word}[/bold]  score={entry.score}  obscurity={entry.obscurity}  "
        f"source={entry.source}"
    )


# ----------------------------------------------------------------------
# bg puzzle templates
# ----------------------------------------------------------------------


@template_app.command("build")
def templates_build(
    per_band: Annotated[int, typer.Option(help="Templates per difficulty band")] = 50,
    size: Annotated[int, typer.Option()] = 15,
    seed: Annotated[int, typer.Option()] = 0,
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Generate and vet a template library. Every template emitted is legal."""
    out = out or settings().template_path
    started = time.monotonic()
    library = tpl.generate_library(size=size, per_band=per_band, seed=seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([t.model_dump() for t in library], indent=2))
    console.print(
        f"Wrote [bold]{len(library)}[/bold] templates to {out} in {time.monotonic() - started:.1f}s"
    )


@template_app.command("vet")
def templates_vet(
    budget: Annotated[float, typer.Option(help="Seconds per template")] = 20.0,
    drop: Annotated[bool, typer.Option(help="Remove templates that cannot be filled")] = False,
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Try to fill every template, and record how it went.

    Measured tier-3 fill success is around 70%: the widest grids need long
    entries crossing long entries, and a single-word lexicon does not carry
    enough of them. Making the search cleverer is the expensive answer. The
    cheap one is this — find out once which templates this lexicon can actually
    fill, and stop offering the others. ``--drop`` writes back the survivors,
    which is what turns a 70% tier into a reliable one.
    """
    index = _index()
    library = _templates()
    out = out or settings().template_path

    kept: list[tpl.GridTemplate] = []
    failures = 0
    for template in library:
        pattern = GridPattern.parse(template.pattern)
        result = fill_with_retries(
            pattern,
            index,
            config=FillConfig(tier=template.difficulty_band, time_budget_s=budget, seed=1),
        )
        template.fill_attempts += 1
        if result.ok:
            template.fill_successes += 1
            template.avg_fill_ms = result.elapsed_ms
            kept.append(template)
        else:
            failures += 1
            console.print(f"[yellow]{template.id}: {result.reason}[/yellow]")
            if not drop:
                kept.append(template)

    console.print(
        f"{len(library) - failures}/{len(library)} fillable"
        + (f", dropped {failures}" if drop else "")
    )
    if drop or out != settings().template_path:
        out.write_text(json.dumps([t.model_dump() for t in kept], indent=2))
        console.print(f"Wrote {len(kept)} templates to {out}")


@template_app.command("show")
def templates_show(template_id: str) -> None:
    """Print one template's grid."""
    for template in _templates():
        if template.id == template_id:
            console.print(GridPattern.parse(template.pattern).to_text())
            console.print(
                f"{template.word_count} words, {template.black_count} black, "
                f"band {template.difficulty_band}"
            )
            return
    err.print(f"[red]No template {template_id!r}[/red]")
    raise typer.Exit(code=1)


def _templates() -> list[tpl.GridTemplate]:
    path = settings().template_path
    if not path.exists():
        err.print(
            f"[red]No template library at {path}. Build one with `bg puzzle templates build`."
        )
        raise typer.Exit(code=1)
    return [tpl.GridTemplate.model_validate(row) for row in json.loads(path.read_text())]


# ----------------------------------------------------------------------
# bg puzzle fill / render / qa
# ----------------------------------------------------------------------


@puzzle_app.command("fill")
def puzzle_fill(
    tier: Annotated[int, typer.Option(help="1 easiest, 3 hardest")] = 2,
    theme_entries: Annotated[str | None, typer.Option(help="Comma-separated theme answers")] = None,
    template_id: Annotated[str | None, typer.Option(help="Use a specific template")] = None,
    seed: Annotated[int, typer.Option()] = 0,
    budget: Annotated[float, typer.Option("--budget", help="Seconds")] = 30.0,
    out: Annotated[Path | None, typer.Option(help="Write the puzzle as JSON")] = None,
) -> None:
    """Fill a grid, optionally around a set of theme entries."""
    index = _index()
    library = _templates()

    candidates = [t for t in library if t.difficulty_band == tier]
    if template_id:
        candidates = [t for t in library if t.id == template_id]
    if not candidates:
        err.print(f"[red]No template for tier {tier}[/red]")
        raise typer.Exit(code=1)

    answers = [w.strip().upper() for w in (theme_entries or "").split(",") if w.strip()]
    template = candidates[seed % len(candidates)]
    pattern = GridPattern.parse(template.pattern)

    theme: dict[str, str] = {}
    if answers:
        placement = tpl.place_theme(pattern, [len(a) for a in answers])
        if placement is None:
            err.print(
                f"[red]Cannot place theme entries of lengths "
                f"{[len(a) for a in answers]} in template {template.id}[/red]"
            )
            raise typer.Exit(code=1)
        theme = {placement[i]: answer for i, answer in enumerate(answers)}

    result = fill_with_retries(
        pattern,
        index,
        theme=theme,
        config=FillConfig(tier=tier, seed=seed, time_budget_s=budget),
    )

    if not result.ok or result.fill is None:
        err.print(f"[red]Fill failed after {result.elapsed_ms}ms: {result.reason}[/red]")
        raise typer.Exit(code=1)

    console.print(result.fill.grid_text())
    console.print(
        f"\n{template.id} · {result.elapsed_ms}ms · {result.attempts} attempt(s) · "
        f"{result.nodes} nodes"
    )
    if result.quality is not None:
        quality = result.quality
        colour = "green" if quality.passes else "yellow"
        console.print(
            f"[{colour}]mean score {quality.mean_score}, {len(quality.obscure_entries)} "
            f"obscure entr(y/ies)[/{colour}]"
        )
        for failure in quality.failures:
            console.print(f"  [yellow]{failure}[/yellow]")

    if out:
        puzzle = Puzzle(
            pattern=pattern,
            answers=result.fill.answers,
            theme_slots=tuple(theme),
            tier=tier,
        )
        out.write_text(json.dumps(_as_json(puzzle, template.id), indent=2))
        console.print(f"\nWrote {out}")


@puzzle_app.command("render")
def puzzle_render(path: Path) -> None:
    """Print a saved puzzle: grid, then entries by direction."""
    puzzle = _read_puzzle(path)
    console.print(puzzle.fill.grid_text())
    console.print()

    table = Table(show_header=True)
    table.add_column("slot")
    table.add_column("answer")
    table.add_column("clue")
    for slot in puzzle.pattern.slots:
        answer = puzzle.answers.get(slot.id, "")
        marker = " ★" if slot.id in puzzle.theme_slots else ""
        table.add_row(slot.id + marker, answer, puzzle.clues.get(slot.id, ""))
    console.print(table)


@puzzle_app.command("qa")
def puzzle_qa(path: Path) -> None:
    """Run the mechanical checks against a saved puzzle."""
    puzzle = _read_puzzle(path)
    report = run_checks(puzzle, index=_index())

    for defect in report.defects:
        colour = {Severity.BLOCKER: "red", Severity.MAJOR: "yellow", Severity.MINOR: "dim"}[
            defect.severity
        ]
        console.print(f"[{colour}]{defect}[/{colour}]")
    for name, why in report.skipped:
        console.print(f"[dim]skipped {name}: {why}[/dim]")

    if report.publishable:
        console.print("[green]clean — every check ran and passed[/green]")
    elif not report.defects:
        console.print(
            f"[yellow]no defects, but {len(report.skipped)} check(s) could not run[/yellow]"
        )
    if report.blockers:
        raise typer.Exit(code=1)


def _as_json(puzzle: Puzzle, template_id: str) -> dict[str, object]:
    return {
        "template_id": template_id,
        "tier": puzzle.tier,
        "pattern": puzzle.pattern.to_text(newlines=False),
        "answers": puzzle.answers,
        "clues": puzzle.clues,
        "theme_slots": list(puzzle.theme_slots),
        "theme_title": puzzle.theme_title,
    }


def _read_puzzle(path: Path) -> Puzzle:
    if not path.exists():
        err.print(f"[red]No puzzle at {path}[/red]")
        raise typer.Exit(code=1)
    raw = json.loads(path.read_text())
    return Puzzle(
        pattern=GridPattern.parse(raw["pattern"]),
        answers=raw["answers"],
        clues=raw.get("clues", {}),
        theme_slots=tuple(raw.get("theme_slots", ())),
        theme_title=raw.get("theme_title"),
        tier=raw.get("tier", 2),
    )


__all__ = ["puzzle_app"]
