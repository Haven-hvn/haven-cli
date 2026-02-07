"""Haven jobs command - Manage scheduled jobs."""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Manage scheduled jobs for plugin polling.")
console = Console()


@app.command("list")
def list_jobs(
    status: Optional[str] = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status (active, paused, all).",
    ),
) -> None:
    """List all scheduled jobs.
    
    Example:
        haven jobs list
        haven jobs list --status active
    """
    from haven_cli.scheduler.job_scheduler import get_scheduler
    
    scheduler = get_scheduler()
    jobs = scheduler.jobs
    
    if status:
        if status == "active":
            jobs = [j for j in jobs if j.enabled]
        elif status == "paused":
            jobs = [j for j in jobs if not j.enabled]
    
    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Plugin", style="magenta")
    table.add_column("Schedule", style="green")
    table.add_column("On Success", style="yellow")
    table.add_column("Status", style="bold")
    table.add_column("Last Run")
    table.add_column("Next Run")
    
    for job in jobs:
        status_str = "[green]active[/green]" if job.enabled else "[yellow]paused[/yellow]"
        last_run = job.last_run.strftime("%Y-%m-%d %H:%M") if job.last_run else "Never"
        next_run = job.next_run.strftime("%Y-%m-%d %H:%M") if job.next_run else "N/A"
        
        table.add_row(
            str(job.job_id)[:8],
            job.plugin_name,
            job.schedule,
            job.on_success.value,
            status_str,
            last_run,
            next_run,
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
) -> None:
    """Create a new scheduled job.
    
    Example:
        haven jobs create --plugin YouTubePlugin --schedule "0 * * * *"
        haven jobs create --plugin BitTorrentPlugin --schedule "*/30 * * * *" --on-success archive_all
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
    
    # Create job
    scheduler = get_scheduler()
    job = RecurringJob(
        name=name or f"{plugin} job",
        plugin_name=plugin,
        schedule=schedule,
        on_success=action,
    )
    
    scheduler.add_job(job)
    
    console.print(f"[green]✓[/green] Job created: {job.job_id}")
    console.print(f"  Plugin: {plugin}")
    console.print(f"  Schedule: {schedule}")
    console.print(f"  Next run: {job.next_run}")


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
    
    scheduler = get_scheduler()
    
    # Find job
    matching_jobs = [j for j in scheduler.jobs if str(j.job_id).startswith(job_id)]
    
    if not matching_jobs:
        console.print(f"[red]Job not found: {job_id}[/red]")
        raise typer.Exit(code=1)
    
    job = matching_jobs[0]
    console.print(f"[bold]Running job:[/bold] {job.name}")
    
    async def execute() -> None:
        result = await scheduler.run_job_now(job.job_id)
        
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
    
    table = Table(title=f"Job History{f' for {job_id}' if job_id else ''}")
    table.add_column("Job ID", style="cyan")
    table.add_column("Started", style="green")
    table.add_column("Duration")
    table.add_column("Status", style="bold")
    table.add_column("Sources Found")
    table.add_column("Archived")
    
    for record in history:
        duration = ""
        if record.completed_at:
            delta = record.completed_at - record.started_at
            duration = f"{delta.total_seconds():.1f}s"
        
        status = "[green]success[/green]" if record.success else "[red]failed[/red]"
        
        table.add_row(
            str(record.job_id)[:8],
            record.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            duration,
            status,
            str(record.sources_found),
            str(record.sources_archived),
        )
    
    console.print(table)
