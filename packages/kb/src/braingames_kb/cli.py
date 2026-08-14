"""``bg`` — the knowledge base and cost CLI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from braingames_core.config import settings
from braingames_core.costs import make_sink, rollup
from braingames_puzzle.cli import puzzle_app

from .assembler import SpineTooLargeError, assemble, fingerprint
from .evalkit import GoldenSet, StubRunner, run_eval
from .lifecycle import review, scaffold, usage_from_log
from .loader import KnowledgeBase, KnowledgeBaseError
from .models import Kind, Stage
from .router import STAGE_BUDGETS, RequiredDocsOverflowError, RoutingContext, explain
from .router import route as route_docs
from .skills import check, sync
from .tools import TOOL_DEF
from .volatile import VolatilePromptError

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Braingames developer CLI")
kb_app = typer.Typer(no_args_is_help=True, help="Knowledge base operations")
app.add_typer(kb_app, name="kb")

# Puzzle construction lives in its own package but hangs off the same CLI:
# one command to learn, one place for shell completion.
app.add_typer(puzzle_app, name="puzzle")

console = Console()
err = Console(stderr=True)


def _load(strict: bool = False) -> KnowledgeBase:
    try:
        return KnowledgeBase.load(settings().knowledge_dir, strict=strict)
    except KnowledgeBaseError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


def _ctx(tags: list[str] | None, tier: int | None, motif: str | None) -> RoutingContext:
    return RoutingContext(cohort_tags=tags or [], tier=tier, motif=motif)


# ----------------------------------------------------------------------
# kb validate
# ----------------------------------------------------------------------


@kb_app.command("validate")
def kb_validate(
    warnings_as_errors: Annotated[bool, typer.Option("--strict")] = False,
) -> None:
    """Validate every document. Exits non-zero on any error."""
    kb = _load(strict=False)

    for defect in kb.defects:
        colour = "red" if defect.severity == "error" else "yellow"
        console.print(f"[{colour}]{defect}[/{colour}]")

    stats = kb.stats()
    console.print(
        f"\n[bold]{stats['total']}[/bold] documents "
        f"([green]{stats['active']} active[/green]), "
        f"{len(kb.errors)} error(s), {len(kb.warnings)} warning(s)"
    )
    console.print(f"kb_version: [cyan]{kb.version()}[/cyan]")

    if kb.errors or (warnings_as_errors and kb.warnings):
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------
# kb list / index
# ----------------------------------------------------------------------


@kb_app.command("list")
def kb_list(
    stage: Annotated[Stage | None, typer.Option(help="Filter by pipeline stage")] = None,
) -> None:
    """List documents with their status and measured token cost."""
    kb = _load()
    docs = kb.active(stage) if stage else kb.all()

    table = Table(title=f"Knowledge base{f' — {stage.value}' if stage else ''}")
    table.add_column("ref", style="cyan")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("tokens", justify="right")
    table.add_column("pri", justify="right")
    table.add_column("triggers", overflow="fold")

    for doc in docs:
        t = doc.meta.triggers
        trig = (
            "always"
            if t.always
            else ", ".join(
                filter(
                    None,
                    [
                        ",".join(t.tags),
                        f"tiers={t.tiers}" if t.tiers else "",
                        ",".join(t.motif_keywords),
                    ],
                )
            )
        )
        tok = str(doc.meta.tokens) if doc.meta.tokens is not None else "—"
        if doc.tokens_are_stale and doc.meta.tokens is not None:
            tok = f"[yellow]{tok}?[/yellow]"
        table.add_row(
            doc.ref,
            doc.meta.kind.value,
            doc.meta.status.value,
            tok,
            str(doc.meta.priority),
            trig or "—",
        )
    console.print(table)


@kb_app.command("index")
def kb_index(stage: Annotated[Stage, typer.Option()]) -> None:
    """Print the index block exactly as the model receives it."""
    from .assembler import render_index

    kb = _load()
    text, refs = render_index(kb, stage)
    console.print(text or "[dim](no routable documents for this stage)[/dim]")
    console.print(f"\n[dim]{len(refs)} document(s) listed[/dim]")


# ----------------------------------------------------------------------
# kb route / assemble
# ----------------------------------------------------------------------


@kb_app.command("route")
def kb_route(
    stage: Annotated[Stage, typer.Option()],
    tag: Annotated[list[str] | None, typer.Option("--tag", help="Cohort tag; repeatable")] = None,
    tier: Annotated[int | None, typer.Option()] = None,
    motif: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Show what routing selects, and why."""
    kb = _load()
    ctx = _ctx(tag, tier, motif)
    try:
        result = route_docs(kb, stage, ctx)
    except RequiredDocsOverflowError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"Routing — {stage.value}")
    table.add_column("ref", style="cyan")
    table.add_column("selected")
    table.add_column("reason")
    table.add_column("tokens", justify="right")

    selected = {d.ref for d in result.selected}
    for ref, reason in explain(kb, stage, ctx):
        doc = kb.get(ref)
        mark = "[green]yes[/green]" if ref in selected else "[dim]no[/dim]"
        table.add_row(ref, mark, reason, str(doc.cost_tokens()) if doc else "—")

    console.print(table)
    console.print(
        f"budget {result.tokens_used}/{result.budget} tokens "
        f"({STAGE_BUDGETS.get(stage, 0)} default), {len(result.dropped)} dropped"
    )
    for doc, why in result.dropped:
        console.print(f"  [yellow]dropped[/yellow] {doc.ref}: {why}")


@kb_app.command("assemble")
def kb_assemble(
    stage: Annotated[Stage, typer.Option()],
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    tier: Annotated[int | None, typer.Option()] = None,
    motif: Annotated[str | None, typer.Option()] = None,
    show: Annotated[bool, typer.Option("--show", help="Print full prompt text")] = False,
) -> None:
    """Assemble the system blocks for one call."""
    kb = _load()
    try:
        prompt = assemble(kb, stage, _ctx(tag, tier, motif))
    except (RequiredDocsOverflowError, SpineTooLargeError, VolatilePromptError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    for i, block in enumerate(prompt.blocks):
        cc = block.get("cache_control")
        marker = f" [magenta]cache_control={cc}[/magenta]" if cc else ""
        text = str(block["text"])
        console.print(f"[bold]block {i}[/bold] — {len(text)} chars{marker}")
        if show:
            console.print(text)
            console.print()

    console.print(f"\nkb_version:  [cyan]{prompt.kb_version}[/cyan]")
    console.print(f"fingerprint: [cyan]{fingerprint(prompt)[:16]}[/cyan]")
    console.print(f"docs_used:   {', '.join(prompt.docs_used) or '—'}")
    console.print(f"~tokens:     {prompt.approx_tokens()}")


# ----------------------------------------------------------------------
# kb tokens
# ----------------------------------------------------------------------


@kb_app.command("tokens")
def kb_tokens(
    all_docs: Annotated[bool, typer.Option("--all", help="Re-measure everything")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Measure token counts with count_tokens and write them back."""
    from braingames_core.llm import LLMClient

    from .tokens import refresh

    kb = _load()
    stale = [d for d in kb.all() if all_docs or d.tokens_are_stale]
    if not stale:
        console.print("[green]All token counts are current.[/green]")
        return

    console.print(f"Measuring {len(stale)} document(s) against {settings().model_token_reference}")
    if dry_run:
        for doc in stale:
            console.print(f"  would measure {doc.ref} (current: {doc.meta.tokens})")
        return

    try:
        results = refresh(kb, LLMClient(), only_stale=not all_docs)
    except Exception as exc:
        err.print(f"[red]Token measurement failed: {exc}[/red]")
        err.print("[dim]Needs Anthropic credentials (ANTHROPIC_API_KEY or `ant auth login`).[/dim]")
        raise typer.Exit(code=1) from exc

    table = Table(title="Token counts")
    table.add_column("ref", style="cyan")
    table.add_column("was", justify="right")
    table.add_column("now", justify="right")
    for r in results:
        table.add_row(r.ref, str(r.previous or "—"), str(r.measured))
    console.print(table)


# ----------------------------------------------------------------------
# kb eval
# ----------------------------------------------------------------------


@kb_app.command("eval")
def kb_eval(
    doc: Annotated[str, typer.Option("--doc", help="Document id or ref")],
    against: Annotated[str, typer.Option("--against", help="Golden set name")],
) -> None:
    """Run a golden set with and without a document, and report the deltas."""
    kb = _load()
    golden_root = settings().eval_dir / "golden"
    sets = GoldenSet.discover(golden_root)

    if against not in sets:
        err.print(f"[red]No golden set {against!r} in {golden_root}[/red]")
        if sets:
            err.print(f"Available: {', '.join(sorted(sets))}")
        raise typer.Exit(code=1)

    golden = sets[against]
    try:
        report = run_eval(kb, doc, golden, StubRunner(golden.metrics))
    except (KeyError, ValueError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"{doc} vs {golden.name} ({report.scenarios} scenarios)")
    table.add_column("metric")
    table.add_column("without", justify="right")
    table.add_column("with", justify="right")
    table.add_column("delta", justify="right")
    table.add_column("", justify="center")

    for d in report.deltas:
        sign = "+" if d.delta >= 0 else ""
        table.add_row(
            d.spec.name + (" [dim](gate)[/dim]" if d.spec.gate else ""),
            format(d.without, d.spec.fmt),
            format(d.with_, d.spec.fmt),
            f"{sign}{format(d.delta, d.spec.fmt)}",
            d.verdict_symbol,
        )
    console.print(table)

    colour = {"promote": "green", "revise": "yellow", "reject": "red"}[report.verdict]
    console.print(
        f"\n[{colour} bold]VERDICT: {report.verdict}[/{colour} bold]  ({report.rationale})"
    )
    console.print(
        "[dim]StubRunner: exercises the harness, measures nothing about puzzle quality. "
        "Real runner lands in P3.6.[/dim]"
    )


# ----------------------------------------------------------------------
# kb tool / stats
# ----------------------------------------------------------------------


@kb_app.command("tool")
def kb_tool() -> None:
    """Print the read_knowledge tool definition sent to the model."""
    import json

    console.print_json(json.dumps(TOOL_DEF))


@kb_app.command("stats")
def kb_stats() -> None:
    """Per-stage spine size and budget headroom."""
    from .assembler import SPINE_CAP, render_spine

    kb = _load()
    table = Table(title="Knowledge base by stage")
    table.add_column("stage", style="cyan")
    table.add_column("spine docs", justify="right")
    table.add_column("spine tokens", justify="right")
    table.add_column("routable", justify="right")
    table.add_column("budget", justify="right")

    for stage in Stage:
        _, refs, cost = render_spine(kb, stage)
        over = cost > SPINE_CAP
        table.add_row(
            stage.value,
            str(len(refs)),
            f"[red]{cost}[/red]" if over else str(cost),
            str(len(kb.routable(stage))),
            str(STAGE_BUDGETS.get(stage, 0)),
        )
    console.print(table)
    console.print(f"spine cap: {SPINE_CAP} tokens per stage")


# ----------------------------------------------------------------------
# costs
# ----------------------------------------------------------------------


@app.command("costs")
def costs(
    days: Annotated[int, typer.Option(help="Look back this many days")] = 7,
) -> None:
    """Model spend by stage. The number that decides whether this business works."""
    cfg = settings()
    sink = make_sink(cfg.cost_sink, cfg.cost_log_path)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = rollup(sink.read_all(), since=since)

    if not rows:
        console.print(f"[dim]No calls recorded in the last {days} day(s).[/dim]")
        console.print(f"[dim]Sink: {cfg.cost_sink} at {cfg.cost_log_path}[/dim]")
        return

    table = Table(title=f"Model spend — last {days} day(s)")
    table.add_column("stage", style="cyan")
    table.add_column("model")
    table.add_column("calls", justify="right")
    table.add_column("in", justify="right")
    table.add_column("cached", justify="right")
    table.add_column("out", justify="right")
    table.add_column("cache hit", justify="right")
    table.add_column("USD", justify="right")

    total = sum(r.cost_usd for r in rows)
    for r in rows:
        # A cache hit ratio trending to zero is the canary for something
        # volatile creeping in above a cache breakpoint.
        ratio = r.cache_hit_ratio
        colour = "green" if ratio > 0.5 else ("yellow" if ratio > 0.15 else "red")
        table.add_row(
            r.stage,
            r.model,
            str(r.calls),
            f"{r.input_tokens:,}",
            f"{r.cached_tokens:,}",
            f"{r.output_tokens:,}",
            f"[{colour}]{ratio:.0%}[/{colour}]",
            f"${r.cost_usd:.4f}",
        )
    console.print(table)
    console.print(f"[bold]Total: ${total:.4f}[/bold]")


if __name__ == "__main__":
    app()


# ----------------------------------------------------------------------
# kb new / review / sync-skills
# ----------------------------------------------------------------------


@kb_app.command("new")
def kb_new(
    doc_id: Annotated[str, typer.Argument(help="Slug, e.g. scene-ribbon-flow")],
    kind: Annotated[Kind, typer.Option(help="Which kind of knowledge this is")],
    stage: Annotated[list[Stage], typer.Option("--stage", help="Repeatable")],
    summary: Annotated[str, typer.Option(help="Index line. Lead with 'Use when...'")],
    owner: Annotated[str, typer.Option()] = "unassigned",
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    motif: Annotated[list[str] | None, typer.Option("--motif-keyword")] = None,
    tier: Annotated[list[int] | None, typer.Option("--tier")] = None,
    always: Annotated[bool, typer.Option("--always")] = False,
    required: Annotated[bool, typer.Option("--required")] = False,
    code: Annotated[bool, typer.Option("--code", help="Create a .ts template")] = False,
) -> None:
    """Scaffold a new draft document with valid frontmatter."""
    try:
        path = scaffold(
            settings().knowledge_dir,
            doc_id=doc_id,
            kind=kind,
            stages=list(stage),
            summary=summary,
            owner=owner,
            always=always,
            tags=tag,
            motif_keywords=motif,
            tiers=tier,
            required=required,
            suffix=".ts" if code else ".md",
        )
    except (FileExistsError, KeyError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Created[/green] {path.relative_to(settings().knowledge_dir.parent)}")
    console.print(
        "\nIt is a [yellow]draft[/yellow]: drafts are never routed into prompts.\n"
        "Write the body, then `bg kb eval` it to earn [green]active[/green]."
    )


@kb_app.command("review")
def kb_review(
    usage_log: Annotated[Path | None, typer.Option(help="read_knowledge log (JSONL)")] = None,
) -> None:
    """The decay pass: what to re-evaluate, retire, or delete.

    A knowledge base that only grows becomes noise, and noise degrades output.
    Nothing breaks when you skip this, which is exactly why it needs a command.
    """
    kb = _load()
    usage = usage_from_log(usage_log) if usage_log else None
    report = review(kb, usage=usage)

    sections = [
        ("Review overdue", report.overdue, "yellow"),
        ("No evidence", report.unevidenced, "yellow"),
        ("Tokens unmeasured", report.unmeasured, "cyan"),
        ("Never read", report.unread, "yellow"),
        ("Stale drafts", report.stale_drafts, "cyan"),
        ("Deprecated — deletable", report.deletable, "red"),
    ]

    for title, items, colour in sections:
        if not items:
            continue
        table = Table(title=f"{title} ({len(items)})")
        table.add_column("ref", style="cyan")
        table.add_column("reason", style=colour)
        table.add_column("action", overflow="fold")
        for item in items:
            table.add_row(item.ref, item.reason, item.action)
        console.print(table)

    for stage_, cost, refs in report.oversized_spines:
        console.print(
            f"[red]Spine over cap[/red] — {stage_.value}: {cost} tokens across "
            f"{len(refs)} documents. Move situational parts into patterns/."
        )

    if not report.usage_available:
        console.print(
            "\n[dim]No usage log supplied, so the unread check was skipped rather than "
            "guessed at. Pass --usage-log once generation runs are producing one.[/dim]"
        )

    if report.is_clean:
        console.print("[green]Nothing to review. The knowledge base is current.[/green]")


@kb_app.command("sync-skills")
def kb_sync_skills(
    check_only: Annotated[
        bool, typer.Option("--check", help="Fail if stale; write nothing")
    ] = False,
) -> None:
    """Publish knowledge documents as Claude Code skills under .claude/skills/."""
    kb = _load()
    root = settings().knowledge_dir.parent / ".claude" / "skills"

    if check_only:
        stale = check(kb, root)
        if stale:
            err.print(f"[red]Skills are stale: {', '.join(stale)}[/red]")
            err.print("[dim]Run `bg kb sync-skills` and commit the result.[/dim]")
            raise typer.Exit(code=1)
        console.print("[green]Skills are up to date.[/green]")
        return

    result = sync(kb, root)
    for path in result.written:
        console.print(f"[green]wrote[/green]  {path.relative_to(root.parent.parent)}")
    for path in result.removed:
        console.print(f"[red]pruned[/red] {path.relative_to(root.parent.parent)}")
    for name in result.empty:
        console.print(f"[yellow]empty[/yellow]  {name} — no active documents for its stages yet")
    if not result.changed:
        console.print("[dim]Already up to date.[/dim]")
