"""Haven prowlarr command - inspect Prowlarr and manage Prowlarr archiving.

All commands read ``[plugins.settings.ProwlarrPlugin]`` from the Haven
config; the API key comes from ``$PROWLARR_API_KEY`` (or the configured
``api_key_env`` / ``api_key_file``).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, List, Optional

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from haven_cli.plugins.builtin.prowlarr import ProwlarrPlugin

app = typer.Typer(help="Search Prowlarr indexers and manage Prowlarr archiving.")
console = Console()


def _plugin() -> ProwlarrPlugin:
    from haven_cli.plugins.builtin.prowlarr import ProwlarrPlugin
    from haven_cli.plugins.builtin.prowlarr.settings import SettingsError

    plugin = ProwlarrPlugin.from_haven_config()
    try:
        settings = plugin.settings()
    except SettingsError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if not settings.api_key:
        console.print(
            "[red]Prowlarr API key not found.[/red] "
            "Export PROWLARR_API_KEY or set api_key_env / api_key_file."
        )
        raise typer.Exit(code=2)
    return plugin


def _run(coro: Any) -> Any:
    from haven_cli.plugins.builtin.prowlarr.settings import SettingsError
    from haven_cli.services.prowlarr import ProwlarrError

    try:
        return asyncio.run(coro)
    except (ProwlarrError, SettingsError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


def _size(num: Optional[int]) -> str:
    if not num:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "-"


@app.command("status")
def status() -> None:
    """Check Prowlarr connectivity and each configured download backend."""
    plugin = _plugin()
    settings = plugin.settings()

    async def _check() -> None:
        from haven_cli.acquisition.clients import ClientError
        from haven_cli.plugins.builtin.prowlarr.backends import make_backend

        async with plugin._client(settings) as client:
            info = await client.system_status()
            indexers = await client.indexers()
        enabled = sum(1 for ix in indexers if ix.enabled)
        console.print(
            f"[green]✓[/green] Prowlarr {info.get('version', '?')} at {settings.base_url} "
            f"— {enabled}/{len(indexers)} indexers enabled"
        )
        used = {s.torrent_client for s in settings.searches} | {
            s.usenet_client for s in settings.searches
        }
        if settings.watch_dir is not None:
            used.add("watch")
        for name in sorted(used - {"none", "prowlarr"}):
            backend = (
                make_backend(name, settings, plugin._selection(settings.searches[0], settings))
                if settings.searches
                else None
            )
            if backend is None:
                continue
            try:
                console.print(f"[green]✓[/green] {name}: {await backend.health()}")
            except ClientError as exc:
                console.print(f"[red]✗[/red] {name}: {exc}")
            finally:
                await backend.aclose()
        for warning in settings.warnings:
            console.print(f"[yellow]![/yellow] {warning}")

    _run(_check())


@app.command("indexers")
def indexers(
    all_: bool = typer.Option(False, "--all", "-a", help="Include disabled indexers."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """List Prowlarr indexers with ids, protocol, search modes and categories."""
    plugin = _plugin()

    async def _list() -> list[Any]:
        async with plugin._client(plugin.settings()) as client:
            return await client.indexers()

    rows = [ix for ix in _run(_list()) if all_ or ix.enabled]
    if as_json:
        console.print_json(
            json.dumps(
                [
                    {
                        "id": ix.id,
                        "name": ix.name,
                        "definition": ix.definition_name,
                        "enabled": ix.enabled,
                        "protocol": ix.protocol,
                        "privacy": ix.privacy,
                        "tags": list(ix.tags),
                        "search_types": list(ix.search_types),
                        "search_params": {k: list(v) for k, v in ix.search_params.items()},
                        "categories": [{"id": c.id, "name": c.name} for c in ix.categories],
                    }
                    for ix in rows
                ]
            )
        )
        return
    table = Table(title="Prowlarr indexers")
    for column in ("ID", "Name", "Protocol", "Privacy", "Enabled", "Search types", "Categories"):
        table.add_column(column)
    for ix in rows:
        table.add_row(
            str(ix.id),
            ix.name,
            ix.protocol,
            ix.privacy or "-",
            "yes" if ix.enabled else "no",
            ", ".join(ix.search_types) or "-",
            ", ".join(f"{c.name} ({c.id})" for c in ix.categories[:6])
            + (" …" if len(ix.categories) > 6 else ""),
        )
    console.print(table)


def _adhoc_spec(**values: Any) -> tuple[ProwlarrPlugin, Any]:
    from haven_cli.plugins.builtin.prowlarr.settings import build_search

    plugin = _plugin()
    row = {k: v for k, v in values.items() if v not in (None, [], "")}
    row.setdefault("name", "cli")
    return plugin, build_search(row, plugin.settings().defaults, where="command line")


@app.command("search")
def search(
    query: str = typer.Argument("", help="Search terms (empty = latest releases)."),
    indexer: Optional[List[str]] = typer.Option(
        None, "--indexer", "-i", help="Indexer id or name (repeatable)."
    ),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Indexer tag label (repeatable)."),
    category: Optional[List[str]] = typer.Option(
        None, "--category", "-c", help="Category id or name (repeatable)."
    ),
    search_type: str = typer.Option(
        "search", "--type", "-t", help="search, tvsearch, movie, music or book."
    ),
    protocol: str = typer.Option("any", "--protocol", help="any, torrent or usenet."),
    limit: int = typer.Option(25, "--limit", "-n", help="Maximum results to show."),
    max_age_hours: float = typer.Option(
        0, "--max-age-hours", help="Only releases newer than this."
    ),
    sort: str = typer.Option(
        "publish_date", "--sort", help="publish_date, seeders, size, grabs, relevance, title."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Run an ad-hoc Prowlarr search (nothing is downloaded)."""
    ids = [int(v) for v in indexer or [] if v.isdigit()]
    names = [v for v in indexer or [] if not v.isdigit()]
    plugin, spec = _adhoc_spec(
        query=query,
        indexer_ids=ids,
        indexers=names,
        indexer_tags=tag or [],
        categories=category or [],
        type=search_type,
        protocol=protocol,
        max_results=limit,
        max_age_hours=max_age_hours,
        sort=sort,
    )

    async def _search() -> list[Any]:
        async with plugin._client(plugin.settings()) as client:
            all_indexers = await client.indexers()
            tags = await client.tags() if spec.indexer_tags else []
            return await plugin.run_search(client, spec, all_indexers, tags)

    releases = _run(_search())
    if as_json:
        from haven_cli.plugins.builtin.prowlarr.plugin import release_to_dict

        console.print_json(json.dumps([release_to_dict(r) for r in releases], default=str))
        return
    table = Table(title=f"{len(releases)} result(s)")
    for column in ("Published", "Title", "Indexer", "Size", "Seeders", "Protocol"):
        table.add_column(column)
    for r in releases:
        table.add_row(
            r.publish_date.strftime("%Y-%m-%d %H:%M") if r.publish_date else "-",
            r.title,
            r.indexer,
            _size(r.size),
            str(r.seeders) if r.seeders is not None else "-",
            r.protocol,
        )
    console.print(table)


@app.command("searches")
def searches() -> None:
    """List saved searches from the config."""
    plugin = _plugin()
    table = Table(title="Saved Prowlarr searches")
    for column in ("Name", "Enabled", "Query", "Type", "Indexers", "Categories", "Fetch", "Max"):
        table.add_column(column)
    for spec in plugin.settings().searches:
        targets = (
            [str(i) for i in spec.indexer_ids]
            + spec.indexers
            + [f"tag:{t}" for t in spec.indexer_tags]
        )
        table.add_row(
            spec.name,
            "yes" if spec.enabled else "no",
            spec.compiled_query() or "(latest)",
            spec.type,
            ", ".join(targets) or f"all ({spec.protocol})",
            ", ".join(str(c) for c in spec.categories) or "-",
            spec.fetch,
            str(spec.max_results),
        )
    console.print(table)


@app.command("preview")
def preview(
    search_name: Optional[List[str]] = typer.Option(
        None, "--search", "-s", help="Saved search name (repeatable)."
    ),
    options_json: Optional[str] = typer.Option(
        None, "--options-json", help="Job options JSON (as for jobs create)."
    ),
) -> None:
    """Dry run: show what a scheduled job would archive (no downloads)."""
    plugin = _plugin()
    options: dict[str, Any] = json.loads(options_json) if options_json else {}
    if search_name:
        options["prowlarr_searches"] = list(search_name)

    async def _discover() -> list[Any]:
        return await plugin.discover_sources_for(options)

    sources = _run(_discover())
    from haven_cli.plugins.builtin.prowlarr.plugin import release_from_dict
    from haven_cli.plugins.builtin.prowlarr.settings import SearchSpec

    table = Table(title=f"{len(sources)} source(s) would be considered")
    for column in ("Title", "Indexer", "Strategy", "Published source", "Key"):
        table.add_column(column)
    for src in sources:
        release = release_from_dict(src.metadata["prowlarr_release"])
        spec = SearchSpec(
            **{
                k: v
                for k, v in src.metadata["prowlarr_spec"].items()
                if k in SearchSpec.__dataclass_fields__
            }
        )
        table.add_row(
            src.title,
            release.indexer,
            plugin._strategy(spec, release),
            src.uri or "(withheld)",
            src.source_id,
        )
    console.print(table)
    console.print("Sources already archived are skipped at run time when on_success = archive_new.")


@app.command("pending")
def pending(
    show_all: bool = typer.Option(False, "--all", "-a", help="Include finished records."),
) -> None:
    """Show in-flight, retrying and failed acquisitions."""
    from datetime import datetime

    from haven_cli.acquisition.state import AcquisitionStore

    plugin = _plugin()
    records = asyncio.run(AcquisitionStore(plugin.settings().state_file).all())
    table = Table(title="Prowlarr acquisitions")
    for column in ("Key", "Title", "Status", "Backend", "Attempts", "Updated", "Last error"):
        table.add_column(column)
    for record in sorted(records, key=lambda r: r.updated_at, reverse=True):
        if record.status == "done" and not show_all:
            continue
        table.add_row(
            record.key,
            str(record.source.get("title", "")) if record.source else "",
            record.status,
            record.backend or "-",
            str(record.attempts),
            datetime.fromtimestamp(record.updated_at).strftime("%Y-%m-%d %H:%M"),
            record.last_error[:80],
        )
    console.print(table)


@app.command("retry")
def retry(
    key: str = typer.Argument(
        ..., help="Record key from 'haven prowlarr pending' (or 'all-failed')."
    ),
) -> None:
    """Forget a failure so the release is attempted again on the next run."""
    from haven_cli.acquisition.state import AcquisitionStore

    plugin = _plugin()
    store = AcquisitionStore(plugin.settings().state_file)

    async def _forget() -> int:
        if key == "all-failed":
            keys = [r.key for r in await store.all() if r.status in ("failed", "retry")]
        else:
            keys = [key] if await store.get(key) else []
        for k in keys:
            await store.remove(k)
        return len(keys)

    count = asyncio.run(_forget())
    console.print(
        f"[green]✓[/green] Cleared {count} record(s)"
        if count
        else f"[yellow]No record {key!r}[/yellow]"
    )


@app.command("schedule")
def schedule(
    cron: str = typer.Option(..., "--schedule", help="Cron expression, e.g. '0 */6 * * *'."),
    search_name: Optional[List[str]] = typer.Option(
        None, "--search", "-s", help="Saved search name (repeatable)."
    ),
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Inline search query (instead of --search)."
    ),
    indexer: Optional[List[str]] = typer.Option(
        None, "--indexer", "-i", help="Inline search: indexer id or name."
    ),
    category: Optional[List[str]] = typer.Option(
        None, "--category", "-c", help="Inline search: category."
    ),
    search_type: str = typer.Option("search", "--type", "-t", help="Inline search: search mode."),
    max_results: int = typer.Option(25, "--max-results", help="Inline search: releases per run."),
    max_age_hours: float = typer.Option(0, "--max-age-hours", help="Inline search: age limit."),
    option: Optional[List[str]] = typer.Option(
        None, "--option", "-o", help="Extra job option KEY=VALUE."
    ),
    on_success: str = typer.Option(
        "archive_new", "--on-success", help="archive_new, archive_all or log_only."
    ),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Job name."),
) -> None:
    """Create a scheduled ProwlarrPlugin job (wrapper around 'haven jobs create')."""
    from croniter import croniter

    from haven_cli.cli.jobs import parse_job_options
    from haven_cli.scheduler.job_scheduler import OnSuccessAction, RecurringJob, get_scheduler

    plugin = _plugin()
    try:
        metadata = parse_job_options(option or [])
        action = OnSuccessAction(on_success)
        croniter(cron)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if search_name and query is not None:
        console.print("[red]Use either --search or --query, not both.[/red]")
        raise typer.Exit(code=1)
    if search_name:
        metadata["prowlarr_searches"] = list(search_name)
    elif query is not None:
        ids = [int(v) for v in indexer or [] if v.isdigit()]
        names = [v for v in indexer or [] if not v.isdigit()]
        inline = {
            "name": name or "scheduled",
            "query": query,
            "indexer_ids": ids,
            "indexers": names,
            "categories": list(category or []),
            "type": search_type,
            "max_results": max_results,
            "max_age_hours": max_age_hours,
        }
        metadata["prowlarr_search"] = {k: v for k, v in inline.items() if v not in (None, [], "")}
    problems = plugin.validate_job_options(metadata) if (search_name or query is not None) else []
    if problems:
        for problem in problems:
            console.print(f"[red]{problem}[/red]")
        raise typer.Exit(code=1)
    job = get_scheduler().add_job(
        RecurringJob(
            name=name or "Prowlarr job",
            plugin_name="ProwlarrPlugin",
            schedule=cron,
            on_success=action,
            metadata=metadata,
        )
    )
    console.print(f"[green]✓[/green] Job created: {job.job_id} (next run {job.next_run})")
