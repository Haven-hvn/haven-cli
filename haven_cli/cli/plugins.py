"""Haven plugins command - Manage plugins."""

from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Manage archiver plugins.")
console = Console()


def _parse_config_value(value: str, key: Optional[str] = None, existing_config: Optional[dict] = None) -> Any:
    """Parse configuration value string to appropriate type.
    
    Args:
        value: The value string to parse
        key: The configuration key name (used to detect list-type fields)
        existing_config: Existing plugin config to check current type
    
    Returns:
        Parsed value with appropriate type (list, bool, int, float, or str)
    """
    # Boolean
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False

    # Number
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass

    # List (comma-separated)
    if "," in value:
        return [v.strip() for v in value.split(",")]

    # Check if this is a list-type field that should always be a list
    # even when only a single value is provided
    if key and _is_list_field(key, existing_config):
        return [value.strip()]

    # String
    return value


def _is_list_field(key: str, existing_config: Optional[dict] = None) -> bool:
    """Check if a configuration field should be treated as a list.
    
    Detects list-type fields by:
    1. Checking if the key name suggests a list (ends with _ids, _list, etc.)
    2. Checking the existing config value type
    
    Args:
        key: The configuration key name
        existing_config: Existing plugin config to check current type
        
    Returns:
        True if the field should be treated as a list
    """
    # Check existing config first (if available)
    if existing_config is not None:
        existing_value = existing_config.get(key)
        if isinstance(existing_value, list):
            return True
    
    # Common list-type field name patterns
    list_patterns = (
        "_ids",      # e.g., channel_ids, video_ids
        "_list",     # e.g., url_list
        "_items",    # e.g., playlist_items
        "_urls",     # e.g., feed_urls
        "_keys",     # e.g., api_keys
        "_tags",     # e.g., filter_tags
        "_paths",    # e.g., watch_paths
    )
    
    return key.endswith(list_patterns)


@app.command("list")
def list_plugins(
    show_disabled: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Show disabled plugins as well.",
    ),
) -> None:
    """List all available plugins.
    
    Example:
        haven plugins list
        haven plugins list --all
    """
    from haven_cli.plugins.registry import get_registry
    from haven_cli.plugins.manager import get_plugin_manager

    registry = get_registry()
    manager = get_plugin_manager()

    # Discover all plugins
    registry.discover_all()

    table = Table(title="Available Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Status", style="bold")
    table.add_column("Description")

    for plugin_info in registry.get_all_info():
        # Check if loaded in manager
        plugin = manager.get_plugin(plugin_info.name)
        if plugin:
            if plugin.enabled:
                status = "[green]active[/green]" if plugin._initialized else "[green]loaded[/green]"
            else:
                status = "[yellow]disabled[/yellow]" if show_disabled else None
                if not show_disabled:
                    continue
        else:
            status = "[dim]available[/dim]"

        if status:  # Only add if we're showing this plugin
            description = plugin_info.description
            if len(description) > 50:
                description = description[:50] + "..."
            
            table.add_row(
                plugin_info.name,
                plugin_info.version,
                status,
                description,
            )

    console.print(table)

    if not registry.get_all_info():
        console.print("[yellow]No plugins found.[/yellow]")
        console.print("Install plugins or add plugin directories to config.")


@app.command("info")
def plugin_info(
    name: str = typer.Argument(
        ...,
        help="Name of the plugin to get info about.",
    ),
) -> None:
    """Show detailed information about a plugin.
    
    Example:
        haven plugins info YouTubePlugin
    """
    from haven_cli.plugins.registry import get_registry
    from haven_cli.plugins.manager import get_plugin_manager

    registry = get_registry()
    info = registry.get_info(name)

    if not info:
        console.print(f"[red]Plugin not found: {name}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]{info.name}[/bold cyan] v{info.version}")
    console.print(f"[dim]by {info.author}[/dim]")
    console.print()
    console.print(info.description)
    console.print()

    # Capabilities
    console.print("[bold]Capabilities:[/bold]")
    for cap in info.capabilities:
        console.print(f"  • {cap.name}")
    console.print()

    # Configuration
    manager = get_plugin_manager()
    plugin = manager.get_plugin(name)

    if plugin:
        console.print("[bold]Current Configuration:[/bold]")
        config = plugin._config or {}
        if config:
            for key, value in config.items():
                # Mask sensitive values
                if "key" in key.lower() or "secret" in key.lower() or "password" in key.lower():
                    value = "***"
                console.print(f"  {key}: {value}")
        else:
            console.print("  [dim]No configuration set[/dim]")
        console.print()
        console.print(f"[bold]Status:[/bold] {'Enabled' if plugin.enabled else 'Disabled'}")
    else:
        console.print("[bold]Status:[/bold] Not loaded")


@app.command("enable")
def enable_plugin(
    name: str = typer.Argument(
        ...,
        help="Name of the plugin to enable.",
    ),
) -> None:
    """Enable a plugin.
    
    Example:
        haven plugins enable YouTubePlugin
    """
    from haven_cli.plugins.manager import get_plugin_manager
    from haven_cli.plugins.registry import get_registry

    manager = get_plugin_manager()

    # Check if already enabled
    existing_plugin = manager.get_plugin(name)
    if existing_plugin and existing_plugin.enabled:
        console.print(f"[yellow]Plugin already enabled: {name}[/yellow]")
        return

    # If plugin exists but disabled, just enable it
    if existing_plugin:
        existing_plugin.enabled = True
        console.print(f"[green]✓[/green] Plugin enabled: {name}")
        return

    # Try to load and register from registry
    registry = get_registry()
    plugin_class = registry.load(name)

    if not plugin_class:
        console.print(f"[red]Plugin not found: {name}[/red]")
        raise typer.Exit(code=1)

    manager.register(plugin_class)
    console.print(f"[green]✓[/green] Plugin enabled: {name}")


@app.command("disable")
def disable_plugin(
    name: str = typer.Argument(
        ...,
        help="Name of the plugin to disable.",
    ),
) -> None:
    """Disable a plugin.
    
    Example:
        haven plugins disable YouTubePlugin
    """
    from haven_cli.plugins.manager import get_plugin_manager

    manager = get_plugin_manager()

    plugin = manager.get_plugin(name)
    if not plugin:
        console.print(f"[yellow]Plugin not enabled: {name}[/yellow]")
        return

    if not plugin.enabled:
        console.print(f"[yellow]Plugin already disabled: {name}[/yellow]")
        return

    plugin.enabled = False
    console.print(f"[green]✓[/green] Plugin disabled: {name}")


@app.command("configure")
def configure_plugin(
    name: str = typer.Argument(
        ...,
        help="Name of the plugin to configure.",
    ),
    key: Optional[str] = typer.Option(
        None,
        "--set",
        "-s",
        help="Set a configuration value (format: key=value).",
    ),
    show: bool = typer.Option(
        False,
        "--show",
        help="Show current configuration.",
    ),
) -> None:
    """Configure a plugin.
    
    Example:
        haven plugins configure YouTubePlugin --show
        haven plugins configure YouTubePlugin --set api_key=YOUR_API_KEY
    """
    from haven_cli.plugins.manager import get_plugin_manager
    from haven_cli.plugins.registry import get_registry
    from haven_cli.config import get_config, save_config

    manager = get_plugin_manager()

    # Ensure plugin exists
    plugin = manager.get_plugin(name)
    if not plugin:
        # Try to load it
        registry = get_registry()
        plugin_class = registry.load(name)
        if not plugin_class:
            console.print(f"[red]Plugin not found: {name}[/red]")
            raise typer.Exit(code=1)
        manager.register(plugin_class)
        plugin = manager.get_plugin(name)

    if show:
        console.print(f"[bold]Configuration for {name}:[/bold]")

        config = plugin._config or {}
        table = Table()
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")

        if config:
            for k, v in config.items():
                # Mask sensitive values
                if "key" in k.lower() or "secret" in k.lower() or "password" in k.lower():
                    v = "***"
                table.add_row(k, str(v))
        else:
            table.add_row("[dim]No configuration", "[dim]set[/dim]")

        console.print(table)
        return

    if key:
        if "=" not in key:
            console.print("[red]Invalid format. Use --set key=value[/red]")
            raise typer.Exit(code=1)

        k, v = key.split("=", 1)
        
        # Parse value (handle lists, bools, numbers)
        # Pass key and existing config to properly detect list-type fields
        existing_config = plugin._config if plugin else None
        parsed_value = _parse_config_value(v, key=k, existing_config=existing_config)

        # Update plugin config
        if plugin._config is None:
            plugin._config = {}
        plugin._config[k] = parsed_value
        plugin.configure({k: parsed_value})

        # Persist to config file
        config = get_config()
        if name not in config.plugins.plugin_settings:
            config.plugins.plugin_settings[name] = {}
        config.plugins.plugin_settings[name][k] = parsed_value
        save_config(config)

        console.print(f"[green]✓[/green] Set {name}.{k} = {parsed_value}")
    else:
        console.print("[yellow]Use --show to view config or --set key=value to update[/yellow]")


@app.command("test")
def test_plugin(
    name: str = typer.Argument(
        ...,
        help="Name of the plugin to test.",
    ),
    discover: bool = typer.Option(
        False,
        "--discover",
        "-d",
        help="Test discovery functionality.",
    ),
    archive_url: Optional[str] = typer.Option(
        None,
        "--archive",
        "-a",
        help="Test archiving with a specific URL.",
    ),
) -> None:
    """Test a plugin's functionality.
    
    Example:
        haven plugins test YouTubePlugin
        haven plugins test YouTubePlugin --discover
        haven plugins test YouTubePlugin --archive https://youtube.com/watch?v=...
    """
    import asyncio
    from haven_cli.plugins.manager import get_plugin_manager
    from haven_cli.plugins.base import MediaSource, PluginCapability

    manager = get_plugin_manager()
    plugin = manager.get_plugin(name)

    if not plugin:
        console.print(f"[red]Plugin not found or not loaded: {name}[/red]")
        raise typer.Exit(code=1)

    async def run_tests() -> None:
        # Initialize
        console.print(f"[bold]Testing {name}...[/bold]")
        console.print()

        # Health check
        console.print("Health check... ", end="")
        try:
            if not plugin._initialized:
                await plugin.initialize()
            healthy = await plugin.health_check()
            if healthy:
                console.print("[green]✓ passed[/green]")
            else:
                console.print("[red]✗ failed[/red]")
                return
        except Exception as e:
            console.print(f"[red]✗ error: {e}[/red]")
            return

        # Discovery test
        if discover:
            if not plugin.has_capability(PluginCapability.DISCOVER):
                console.print("[yellow]Plugin does not support discovery[/yellow]")
            else:
                console.print("Discovery... ", end="")
                try:
                    sources = await plugin.discover_sources()
                    console.print(f"[green]✓ found {len(sources)} sources[/green]")

                    if sources:
                        console.print()
                        table = Table(title="Discovered Sources")
                        table.add_column("ID")
                        table.add_column("Type")
                        table.add_column("Title")

                        for source in sources[:5]:  # Show first 5
                            title = source.metadata.get("title", "")[:40]
                            table.add_row(source.source_id[:12], source.media_type, title)

                        console.print(table)
                        if len(sources) > 5:
                            console.print(f"[dim]... and {len(sources) - 5} more[/dim]")
                except Exception as e:
                    console.print(f"[red]✗ error: {e}[/red]")

        # Archive test
        if archive_url:
            if not plugin.has_capability(PluginCapability.ARCHIVE):
                console.print("[yellow]Plugin does not support archiving[/yellow]")
            else:
                console.print(f"Archive test ({archive_url})... ", end="")
                try:
                    source = MediaSource(
                        source_id="test",
                        media_type="test",
                        uri=archive_url,
                    )
                    result = await plugin.archive(source)
                    if result.success:
                        console.print(f"[green]✓ archived to {result.output_path}[/green]")
                    else:
                        console.print(f"[red]✗ failed: {result.error}[/red]")
                except Exception as e:
                    console.print(f"[red]✗ error: {e}[/red]")

        console.print()
        console.print("[green]All tests completed.[/green]")

    asyncio.run(run_tests())
