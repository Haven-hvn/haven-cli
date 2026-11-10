"""Haven jobs command - Manage scheduled jobs."""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

app = typer.Typer(help="Manage scheduled jobs for plugin polling.")
console = Console()


# Statuses that describe a scheduled (RecurringJob) row.
_SCHEDULE_STATUSES = {"active", "paused", "all"}
# Statuses that describe an upload_jobs row (plan item 8 / B6). Surfacing
# these is required so a stale 'uploading' row (swept to 'failed' at boot)
# is visible via `haven jobs list --status failed` instead of being hidden.
_UPLOAD_STATUSES = {"pending", "uploading", "completed", "failed"}


@app.command("list")
def list_jobs(
    status: Optional[str] = typer.Option(
        None,
        "--status",
        "-s",
        help=(
            "Filter by status. Scheduled-job states: active, paused, all. "
            "Upload-job states: pending, uploading, completed, failed."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON (full IDs; scripted path).",
    ),
) -> None:
    """List scheduled jobs, or Filecoin upload jobs by status.

    Examples:
        haven jobs list
        haven jobs list --status active
        haven jobs list --status failed        # shows failed upload jobs
        haven jobs list --status failed --json
    """
    # B6: reject unknown statuses loudly instead of silently showing
    # everything (the old code fell through with no filtering).
    if status is not None:
        normalized = status.lower()
        if normalized not in _SCHEDULE_STATUSES and normalized not in _UPLOAD_STATUSES:
            console.print(
                f"[red]Unknown status '{status}'. Expected one of: "
                f"{', '.join(sorted(_SCHEDULE_STATUSES | _UPLOAD_STATUSES))}[/red]"
            )
            raise typer.Exit(code=1)
        status = normalized

    if status in _UPLOAD_STATUSES:
        _list_upload_jobs(status, json_output=json_output)
        return

    from haven_cli.scheduler.job_scheduler import get_scheduler

    scheduler = get_scheduler()
    jobs = scheduler.jobs

    if status == "active":
        jobs = [j for j in jobs if j.enabled]
    elif status == "paused":
        jobs = [j for j in jobs if not j.enabled]
    # "all" / None -> no filtering

    rows = []
    for job in jobs:
        state = "active" if job.enabled else "paused"
        rows.append({
            "job_id": str(job.job_id),  # B14: full ID, no truncation
            "plugin": job.plugin_name,
            "schedule": job.schedule,
            "on_success": job.on_success.value,
            "status": state,
            "last_run": job.last_run.isoformat() if job.last_run else None,
            "next_run": job.next_run.isoformat() if job.next_run else None,
        })

    if json_output:
        console.print(json.dumps(rows, indent=2))
        return

    table = Table(title="Scheduled Jobs")
    # B14: no_wrap so Rich shows the full job ID instead of "cf118882…".
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Plugin", style="magenta")
    table.add_column("Schedule", style="green")
    table.add_column("On Success", style="yellow")
    table.add_column("Status", style="bold")
    table.add_column("Last Run")
    table.add_column("Next Run")

    for row in rows:
        status_str = (
            "[green]active[/green]" if row["status"] == "active"
            else "[yellow]paused[/yellow]"
        )
        last_run = row["last_run"][:16].replace("T", " ") if row["last_run"] else "Never"
        next_run = row["next_run"][:16].replace("T", " ") if row["next_run"] else "N/A"
        table.add_row(
            row["job_id"],
            row["plugin"],
            row["schedule"],
            row["on_success"],
            status_str,
            last_run,
            next_run,
        )

    console.print(table)


def _list_upload_jobs(status: str, json_output: bool = False) -> None:
    """Print ``upload_jobs`` rows for a given status (B6).

    This is what makes swept/orphaned upload rows visible:
    ``haven jobs list --status failed`` shows the rows the B6 startup
    sweep marked failed after a killed/cancelled upload.
    """
    from haven_cli.database.connection import get_db_session
    from haven_cli.database.models import UploadJob

    rows = []
    try:
        with get_db_session() as session:
            jobs = (
                session.query(UploadJob)
                .filter(UploadJob.status == status)
                .order_by(UploadJob.created_at.desc())
                .all()
            )
            for job in jobs:
                rows.append({
                    "id": job.id,
                    "video_id": job.video_id,
                    "status": job.status,
                    "stage": job.stage,
                    "progress_percent": job.progress_percent,
                    "remote_cid": job.remote_cid,
                    "error_message": job.error_message,
                    "created_at": (
                        job.created_at.isoformat() if job.created_at else None
                    ),
                })
    except Exception as e:  # pragma: no cover - DB optional in CLI
        console.print(f"[red]Could not query upload jobs: {e}[/red]")
        raise typer.Exit(code=1)

    if json_output:
        console.print(json.dumps(rows, indent=2))
        return

    table = Table(title=f"Upload Jobs (status={status})")
    table.add_column("ID", style="cyan")
    table.add_column("Video ID", style="magenta")
    table.add_column("Status", style="bold")
    table.add_column("Stage")
    table.add_column("Progress")
    table.add_column("CID")
    table.add_column("Error")

    for row in rows:
        progress = (
            f"{row['progress_percent']:.0f}%"
            if row["progress_percent"] is not None else ""
        )
        err = (row["error_message"] or "")[:40]
        table.add_row(
            str(row["id"]),
            str(row["video_id"]),
            row["status"],
            row["stage"] or "",
            progress,
            row["remote_cid"] or "",
            err,
        )

    console.print(table)


@app.command("create")
def create_job(
    plugin: str = typer.Option(
        ...,
        "--plugin",
        "-p",
        help="Plugin name to use for discovery.",
    ),
    schedule: str = typer.Option(
        ...,
        "--schedule",
        "-s",
        help="Cron schedule expression (e.g., '0 * * * *' for hourly).",
    ),
    on_success: str = typer.Option(
        "archive_new",
        "--on-success",
        help="Action on success: archive_all, archive_new, log_only.",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help="Optional job name.",
    ),
    option: Optional[List[str]] = typer.Option(
        None,
        "--option",
        "-o",
        help=(
            "Job option KEY=VALUE (repeatable). VALUE is parsed as JSON when "
            "possible (true, 42, [1,2], {...}), else kept as a string. Options "
            "reach the plugin and override pipeline settings for this job, "
            "e.g. vlm_enabled=false, arkiv_expires_in=31536000."
        ),
    ),
    options_json: Optional[str] = typer.Option(
        None,
        "--options-json",
        help="Job options as one JSON object (merged before --option values).",
    ),
) -> None:
    """Create a new scheduled job.
    
    Example:
        haven jobs create --plugin YouTubePlugin --schedule "0 * * * *"
        haven jobs create --plugin BitTorrentPlugin --schedule "*/30 * * * *" --on-success archive_all
        haven jobs create --plugin ProwlarrPlugin --schedule "0 */6 * * *" \
            -o prowlarr_searches=papers -o arkiv_expires_in=31536000
    """
    from haven_cli.scheduler.job_scheduler import get_scheduler, RecurringJob, OnSuccessAction
    from haven_cli.plugins.registry import get_registry
    
    # Validate plugin exists
    registry = get_registry()
    if plugin not in registry.available_plugins:
        console.print(f"[red]Plugin not found: {plugin}[/red]")
        console.print(f"Available: {', '.join(registry.available_plugins)}")
        raise typer.Exit(code=1)
    
    # Validate on_success
    try:
        action = OnSuccessAction(on_success)
    except ValueError:
        valid = [a.value for a in OnSuccessAction]
        console.print(f"[red]Invalid on-success action. Choose from: {valid}[/red]")
        raise typer.Exit(code=1)
    
    # Validate cron expression
    try:
        from croniter import croniter
        croniter(schedule)
    except ValueError as e:
        console.print(f"[red]Invalid cron expression: {e}[/red]")
        raise typer.Exit(code=1)
    
    try:
        metadata = parse_job_options(option or [], options_json)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    # Let the plugin validate its own options when it can.
    plugin_class = registry.load(plugin)
    validator = getattr(plugin_class, "validate_job_options", None) if plugin_class else None
    if callable(validator):
        problems = validator(metadata)
        if problems:
            for problem in problems:
                console.print(f"[red]{problem}[/red]")
            raise typer.Exit(code=1)

    # Create job
    scheduler = get_scheduler()
    job = RecurringJob(
        name=name or f"{plugin} job",
        plugin_name=plugin,
        schedule=schedule,
        on_success=action,
        metadata=metadata,
    )
    
    scheduler.add_job(job)
    
    console.print(f"[green]✓[/green] Job created: {job.job_id}")
    console.print(f"  Plugin: {plugin}")
    console.print(f"  Schedule: {schedule}")
    if metadata:
        console.print(f"  Options: {', '.join(sorted(metadata))}")
    console.print(f"  Next run: {job.next_run}")


def _parse_option_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def parse_job_options(pairs: List[str], options_json: Optional[str] = None) -> Dict[str, Any]:
    """Build job metadata from ``--options-json`` and repeated ``--option K=V``.

    Raises:
        ValueError: on malformed input.
    """
    metadata: Dict[str, Any] = {}
    if options_json:
        try:
            loaded = json.loads(options_json)
        except ValueError as e:
            raise ValueError(f"--options-json is not valid JSON: {e}") from e
        if not isinstance(loaded, dict):
            raise ValueError("--options-json must be a JSON object")
        metadata.update(loaded)
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--option expects KEY=VALUE, got {pair!r}")
        metadata[key] = _parse_option_value(value)
    return metadata


@app.command("delete")
def delete_job(
    job_id: str = typer.Argument(
        ...,
        help="ID of the job to delete.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Delete a scheduled job.
    
    Example:
        haven jobs delete job-001
        haven jobs delete job-001 --force
    """
    from haven_cli.scheduler.job_scheduler import get_scheduler
    from uuid import UUID
    
    scheduler = get_scheduler()
    
    # Find job by ID prefix
    matching_jobs = [j for j in scheduler.jobs if str(j.job_id).startswith(job_id)]
    
    if not matching_jobs:
        console.print(f"[red]Job not found: {job_id}[/red]")
        raise typer.Exit(code=1)
    
    if len(matching_jobs) > 1:
        console.print(f"[red]Multiple jobs match '{job_id}'. Be more specific.[/red]")
        raise typer.Exit(code=1)
    
    job = matching_jobs[0]
    
    if not force:
        confirm = typer.confirm(f"Delete job '{job.name}' ({job.job_id})?")
        if not confirm:
            raise typer.Abort()
    
    scheduler.remove_job(job.job_id)
    console.print(f"[green]✓[/green] Job deleted: {job.job_id}")


@app.command("run")
def run_job(
    job_id: str = typer.Argument(
        ...,
        help="ID of the job to run immediately.",
    ),
) -> None:
    """Run a job immediately (outside of schedule).
    
    Example:
        haven jobs run job-001
    """
    import asyncio
    from haven_cli.scheduler.job_scheduler import get_scheduler
    from haven_cli.pipeline.manager import create_default_pipeline
    from haven_cli.config import get_config
    from haven_cli.js_runtime.manager import JSBridgeManager
    
    scheduler = get_scheduler()
    
    # Find job
    matching_jobs = [j for j in scheduler.jobs if str(j.job_id).startswith(job_id)]
    
    if not matching_jobs:
        console.print(f"[red]Job not found: {job_id}[/red]")
        raise typer.Exit(code=1)
    
    job = matching_jobs[0]
    console.print(f"[bold]Running job:[/bold] {job.name}")
    
    async def execute() -> None:
        # Initialize JS Bridge for blockchain operations
        config = get_config()
        try:
            debug_mode = (
                os.environ.get("DEBUG") == "1"
                or os.environ.get("LOG_LEVEL", "").lower() == "debug"
            )
            JSBridgeManager.get_instance().configure(
                network_mode=config.blockchain.effective_filecoin_network_mode,
                debug=debug_mode,
            )
            await JSBridgeManager.get_instance().get_bridge()
        except Exception as e:
            console.print(f"[yellow]Warning: JS Bridge initialization failed: {e}[/yellow]")
        
        # Create pipeline manager with config — batched or default
        batch_sync_enabled = getattr(config.pipeline, "batch_sync_enabled", False)
        sync_enabled = getattr(config.pipeline, "sync_enabled", False)
        
        accumulator = None
        flush_queue = None
        
        if batch_sync_enabled and sync_enabled:
            from haven_cli.pipeline.manager import create_batched_pipeline
            # Phase 4: read all three batch-sync knobs from config so
            # HAVEN_BATCH_SYNC_FLUSH_TIMEOUT / HAVEN_BATCH_SYNC_MAX_PENDING
            # actually take effect. The previous implementation only read
            # batch_size and silently fell through to BatchAccumulator's
            # module defaults for the other two.
            batch_size = getattr(config.pipeline, "batch_sync_size", 10)
            flush_timeout = getattr(config.pipeline, "batch_sync_flush_timeout", 18000.0)
            max_pending = getattr(config.pipeline, "batch_sync_max_pending", 50)
            pipeline_manager, accumulator, flush_queue = create_batched_pipeline(
                max_concurrent=4,
                batch_size=batch_size,
                flush_timeout=flush_timeout,
                max_pending=max_pending,
                config=config.__dict__,
            )
            await flush_queue.start()
        else:
            pipeline_manager = create_default_pipeline(
                max_concurrent=4,
                config=config.__dict__,
            )
        
        # Update scheduler with pipeline manager and accumulator
        scheduler._pipeline_manager = pipeline_manager
        scheduler._accumulator = accumulator
        
        # Start background flush loop for batched sync
        async def flush_loop():
            """Poll accumulator and enqueue ready batches."""
            while True:
                try:
                    batch = await accumulator.flush()
                    if batch:
                        await flush_queue.enqueue(batch)
                        console.print(
                            f"[dim]Batch sync: flushed {len(batch)} contexts for attestation + entity creation[/dim]"
                        )
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("Flush loop error: %s", exc)
                    await asyncio.sleep(1.0)
        
        flush_task = None
        if accumulator is not None and flush_queue is not None:
            flush_task = asyncio.create_task(flush_loop())
        
        try:
            result = await scheduler.run_job_now(job.job_id)
        finally:
            # Stop flush loop and drain remaining
            if flush_task is not None:
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass
            
            if accumulator is not None and flush_queue is not None:
                remaining = await accumulator.drain()
                if remaining:
                    await flush_queue.enqueue(remaining)
                    console.print(
                        f"[dim]Batch sync: draining final {len(remaining)} contexts[/dim]"
                    )
                await flush_queue.stop()
        
        # Shutdown JS Bridge
        try:
            await JSBridgeManager.get_instance().shutdown()
        except Exception:
            pass
        
        if result.success:
            console.print(f"[green]✓[/green] Job completed")
            console.print(f"  Sources found: {result.sources_found}")
            console.print(f"  Sources archived: {result.sources_archived}")
        else:
            console.print(f"[red]✗[/red] Job failed: {result.error}")
    
    asyncio.run(execute())


@app.command("pause")
def pause_job(
    job_id: str = typer.Argument(
        ...,
        help="ID of the job to pause.",
    ),
) -> None:
    """Pause a scheduled job.
    
    Example:
        haven jobs pause job-001
    """
    from haven_cli.scheduler.job_scheduler import get_scheduler
    
    scheduler = get_scheduler()
    matching = [j for j in scheduler.jobs if str(j.job_id).startswith(job_id)]
    
    if not matching:
        console.print(f"[red]Job not found: {job_id}[/red]")
        raise typer.Exit(code=1)
    
    if len(matching) > 1:
        console.print(f"[red]Multiple jobs match '{job_id}'. Be more specific.[/red]")
        raise typer.Exit(code=1)
    
    if scheduler.pause_job(matching[0].job_id):
        console.print(f"[green]✓[/green] Job paused")
    else:
        console.print(f"[red]Failed to pause job[/red]")


@app.command("resume")
def resume_job(
    job_id: str = typer.Argument(
        ...,
        help="ID of the job to resume.",
    ),
) -> None:
    """Resume a paused job.
    
    Example:
        haven jobs resume job-001
    """
    from haven_cli.scheduler.job_scheduler import get_scheduler
    
    scheduler = get_scheduler()
    matching = [j for j in scheduler.jobs if str(j.job_id).startswith(job_id)]
    
    if not matching:
        console.print(f"[red]Job not found: {job_id}[/red]")
        raise typer.Exit(code=1)
    
    if len(matching) > 1:
        console.print(f"[red]Multiple jobs match '{job_id}'. Be more specific.[/red]")
        raise typer.Exit(code=1)
    
    if scheduler.resume_job(matching[0].job_id):
        console.print(f"[green]✓[/green] Job resumed")
        console.print(f"  Next run: {matching[0].next_run}")
    else:
        console.print(f"[red]Failed to resume job[/red]")


@app.command("history")
def job_history(
    job_id: Optional[str] = typer.Argument(
        None,
        help="Job ID to show history for (or all if not specified).",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Number of history entries to show.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON (full IDs; scripted path).",
    ),
) -> None:
    """Show job execution history.
    
    Example:
        haven jobs history
        haven jobs history job-001 --limit 20
    """
    from haven_cli.scheduler.job_scheduler import get_scheduler
    from uuid import UUID
    
    scheduler = get_scheduler()
    
    # Get job UUID if provided
    target_job_id = None
    if job_id:
        matching = [j for j in scheduler.jobs if str(j.job_id).startswith(job_id)]
        if matching:
            target_job_id = matching[0].job_id
    
    history = scheduler.get_history(job_id=target_job_id, limit=limit, from_database=True)

    rows = []
    for record in history:
        duration = ""
        if record.completed_at:
            delta = record.completed_at - record.started_at
            duration = f"{delta.total_seconds():.1f}s"
        rows.append({
            # B14: full job ID, no truncation.
            "job_id": str(record.job_id),
            "started": record.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": duration,
            "status": "success" if record.success else "failed",
            "sources_found": record.sources_found,
            "sources_archived": record.sources_archived,
        })

    if json_output:
        console.print(json.dumps(rows, indent=2))
        return

    table = Table(title=f"Job History{f' for {job_id}' if job_id else ''}")
    table.add_column("Job ID", style="cyan", no_wrap=True)
    table.add_column("Started", style="green")
    table.add_column("Duration")
    table.add_column("Status", style="bold")
    table.add_column("Sources Found")
    table.add_column("Archived")

    for row in rows:
        status = (
            "[green]success[/green]" if row["status"] == "success"
            else "[red]failed[/red]"
        )
        table.add_row(
            row["job_id"],
            row["started"],
            row["duration"],
            status,
            str(row["sources_found"]),
            str(row["sources_archived"]),
        )

    console.print(table)
