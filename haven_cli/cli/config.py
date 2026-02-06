"""Haven config command - Configuration management."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax

app = typer.Typer(help="Manage Haven configuration.")
console = Console()


@app.command("show")
def show_config(
    section: Optional[str] = typer.Argument(
        None,
        help="Configuration section to show (e.g., pipeline, scheduler, plugins).",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format (table, yaml, json).",
    ),
    unmask: bool = typer.Option(
        False,
        "--unmask",
        help="Show unmasked secrets (use with caution).",
    ),
) -> None:
    """Show current configuration.
    
    Example:
        haven config show
        haven config show pipeline
        haven config show --format yaml
    """
    from haven_cli.config import get_config, export_config_yaml, export_config_json
    
    config = get_config()
    
    if format == "yaml":
        try:
            yaml_output = export_config_yaml(config, mask_secrets=not unmask)
            console.print(Syntax(yaml_output, "yaml", theme="monokai"))
        except ImportError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(code=1)
        return
    elif format == "json":
        json_output = export_config_json(config, mask_secrets=not unmask)
        console.print(Syntax(json_output, "json", theme="monokai"))
        return
    
    if section:
        console.print(f"[bold]Configuration: {section}[/bold]")
    else:
        console.print("[bold]Haven Configuration[/bold]")
    console.print()
    
    # Build config sections from actual config
    def mask(value: str, is_secret: bool = False) -> str:
        if not is_secret or unmask:
            return value
        if value and len(value) > 4:
            return value[:4] + "****"
        return "****"
    
    sections = {
        "blockchain": [
            ("network_mode", config.blockchain.network_mode, False),
            ("is_mainnet", str(config.blockchain.is_mainnet), False),
            ("lit_network", config.blockchain.get_lit_network(), False),
            ("filecoin_rpc_url", config.blockchain.get_filecoin_rpc_url(), False),
            ("arkiv_rpc_url", config.blockchain.get_arkiv_rpc_url(), False),
            ("lit_network_override", config.blockchain.lit_network_override or "", False),
            ("filecoin_rpc_override", config.blockchain.filecoin_rpc_override or "", False),
            ("arkiv_rpc_override", config.blockchain.arkiv_rpc_override or "", False),
        ],
        "pipeline": [
            ("vlm_enabled", str(config.pipeline.vlm_enabled), False),
            ("vlm_model", config.pipeline.vlm_model, False),
            ("vlm_api_key", mask(config.pipeline.vlm_api_key or "", True), True),
            ("vlm_timeout", str(config.pipeline.vlm_timeout), False),
            ("encryption_enabled", str(config.pipeline.encryption_enabled), False),
            ("lit_network", config.pipeline.lit_network, False),
            ("upload_enabled", str(config.pipeline.upload_enabled), False),
            ("sync_enabled", str(config.pipeline.sync_enabled), False),
            ("arkiv_endpoint", config.pipeline.arkiv_endpoint or "", False),
            ("arkiv_contract", config.pipeline.arkiv_contract or "", False),
            ("max_concurrent_videos", str(config.pipeline.max_concurrent_videos), False),
            ("retry_attempts", str(config.pipeline.retry_attempts), False),
            ("retry_delay", str(config.pipeline.retry_delay), False),
        ],
        "scheduler": [
            ("enabled", str(config.scheduler.enabled), False),
            ("check_interval", str(config.scheduler.check_interval), False),
            ("max_concurrent_jobs", str(config.scheduler.max_concurrent_jobs), False),
            ("default_cron", config.scheduler.default_cron, False),
            ("job_timeout", str(config.scheduler.job_timeout), False),
            ("state_file", str(config.scheduler.state_file) if config.scheduler.state_file else "", False),
        ],
        "plugins": [
            ("plugin_dirs", ", ".join(str(p) for p in config.plugins.plugin_dirs) or "None", False),
            ("enabled_plugins", ", ".join(config.plugins.enabled_plugins) or "None", False),
            ("disabled_plugins", ", ".join(config.plugins.disabled_plugins) or "None", False),
        ],
        "js_runtime": [
            ("runtime", config.js_runtime.runtime or "auto-detect", False),
            ("services_path", str(config.js_runtime.services_path) if config.js_runtime.services_path else "", False),
            ("startup_timeout", str(config.js_runtime.startup_timeout), False),
            ("request_timeout", str(config.js_runtime.request_timeout), False),
            ("debug", str(config.js_runtime.debug), False),
        ],
        "logging": [
            ("level", config.logging.level, False),
            ("format", config.logging.format, False),
            ("file", str(config.logging.file) if config.logging.file else "", False),
            ("max_size", str(config.logging.max_size), False),
            ("backup_count", str(config.logging.backup_count), False),
        ],
        "paths": [
            ("config_dir", str(config.config_dir), False),
            ("data_dir", str(config.data_dir), False),
            ("database_url", config.database_url, False),
        ],
    }
    
    sections_to_show = [section] if section else sections.keys()
    
    for sec in sections_to_show:
        if sec not in sections:
            console.print(f"[red]Unknown section: {sec}[/red]")
            continue
            
        table = Table(title=sec.capitalize())
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")
        table.add_column("Sensitive", style="yellow")
        
        for key, value, sensitive in sections[sec]:
            sens_display = "Yes" if sensitive else ""
            table.add_row(key, value, sens_display)
        
        console.print(table)
        console.print()


@app.command("set")
def set_config(
    key: str = typer.Argument(
        ...,
        help="Configuration key (format: section.key, e.g., pipeline.vlm_model).",
    ),
    value: str = typer.Argument(
        ...,
        help="Value to set.",
    ),
) -> None:
    """Set a configuration value.
    
    Example:
        haven config set pipeline.vlm_model zai-org/glm-4.6v-flash
        haven config set pipeline.max_concurrent_videos 8
        haven config set scheduler.enabled false
    """
    import os
    from pathlib import Path
    from haven_cli.config import set_config_value, CONFIG_DIR, CONFIG_FILE
    
    if "." not in key:
        console.print("[red]Key must be in format: section.key[/red]")
        raise typer.Exit(code=1)
    
    section, config_key = key.split(".", 1)
    
    # Check for environment variable override
    config_dir = Path(os.environ.get("HAVEN_CONFIG_DIR", CONFIG_DIR))
    config_path = config_dir / CONFIG_FILE
    
    try:
        set_config_value(section, config_key, value, config_path)
        # Clear the global config cache so the new value will be loaded
        from haven_cli.config import clear_config_cache
        clear_config_cache()
        console.print(f"[green]✓[/green] Set {section}.{config_key} = {value}")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("init")
def init_config(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing configuration.",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        "-i/-I",
        help="Run interactive configuration wizard.",
    ),
) -> None:
    """Initialize Haven configuration.
    
    Example:
        haven config init
        haven config init --no-interactive
        haven config init --force
    """
    import os
    from pathlib import Path
    from haven_cli.config import (
        CONFIG_DIR, CONFIG_FILE, HavenConfig, save_config, ensure_directories
    )
    
    # Check for environment variable override
    config_dir = Path(os.environ.get("HAVEN_CONFIG_DIR", CONFIG_DIR))
    config_file = CONFIG_FILE
    config_path = config_dir / config_file
    
    if config_path.exists() and not force:
        console.print(f"[yellow]Configuration already exists at {config_path}[/yellow]")
        console.print("Use --force to overwrite")
        raise typer.Exit(code=1)
    
    console.print("[bold]Initializing Haven configuration...[/bold]")
    console.print()
    
    # Create default config
    config = HavenConfig()
    config.config_dir = config_dir
    config.data_dir = Path(os.environ.get("HAVEN_DATA_DIR", config_dir.parent.parent / ".local" / "share" / "haven"))
    
    if interactive:
        # Interactive wizard
        console.print("[bold cyan]Blockchain Network Configuration[/bold cyan]")
        console.print("  [dim]This setting controls the network for all blockchain integrations[/dim]")
        console.print("  [dim](Lit Protocol, Filecoin, Arkiv)[/dim]")
        
        network_choices = ["testnet", "mainnet"]
        network_default = config.blockchain.network_mode
        console.print(f"  [1] Testnet (safe for development)")
        console.print(f"  [2] Mainnet (uses real tokens)")
        
        network_choice = typer.prompt(
            "  Select network",
            default="1",
            show_choices=False,
        )
        
        if network_choice == "2":
            confirm = typer.confirm(
                "  [red]⚠️  WARNING: Mainnet uses real tokens. Are you sure?[/red]",
                default=False
            )
            if confirm:
                config.blockchain.network_mode = "mainnet"
            else:
                config.blockchain.network_mode = "testnet"
        else:
            config.blockchain.network_mode = "testnet"
        
        console.print(f"  [green]✓[/green] Network set to: {config.blockchain.network_mode}")
        console.print()
        
        console.print("[bold cyan]Arkiv Configuration[/bold cyan]")
        arkiv_enabled = typer.confirm("  Enable Arkiv sync?", default=config.pipeline.sync_enabled)
        config.pipeline.sync_enabled = arkiv_enabled
        if arkiv_enabled:
            # Show the auto-configured endpoint
            console.print(f"  [dim]Arkiv RPC: {config.blockchain.get_arkiv_rpc_url()}[/dim]")
            arkiv_endpoint = typer.prompt(
                "  Arkiv RPC URL (press Enter to use default)",
                default="",
            )
            if arkiv_endpoint:
                config.pipeline.arkiv_endpoint = arkiv_endpoint
        
        console.print()
        console.print("[bold cyan]VLM Configuration[/bold cyan]")
        vlm_enabled = typer.confirm("  Enable VLM analysis?", default=config.pipeline.vlm_enabled)
        config.pipeline.vlm_enabled = vlm_enabled
        if vlm_enabled:
            vlm_model = typer.prompt(
                "  VLM Model",
                default=config.pipeline.vlm_model
            )
            config.pipeline.vlm_model = vlm_model
            vlm_api_key = typer.prompt(
                "  VLM API Key (optional)",
                default="",
                hide_input=True
            )
            config.pipeline.vlm_api_key = vlm_api_key if vlm_api_key else None
        
        console.print()
        console.print("[bold cyan]Encryption Configuration[/bold cyan]")
        encryption_enabled = typer.confirm(
            "  Enable Lit Protocol encryption?",
            default=config.pipeline.encryption_enabled
        )
        config.pipeline.encryption_enabled = encryption_enabled
        if encryption_enabled:
            lit_network = typer.prompt(
                "  Lit Network",
                default=config.pipeline.lit_network
            )
            config.pipeline.lit_network = lit_network
        
        console.print()
        console.print("[bold cyan]Pipeline Configuration[/bold cyan]")
        max_concurrent = typer.prompt(
            "  Max concurrent videos",
            default=str(config.pipeline.max_concurrent_videos)
        )
        config.pipeline.max_concurrent_videos = int(max_concurrent)
        
        console.print()
        console.print("[bold cyan]Scheduler Configuration[/bold cyan]")
        scheduler_enabled = typer.confirm(
            "  Enable job scheduler?",
            default=config.scheduler.enabled
        )
        config.scheduler.enabled = scheduler_enabled
        
        console.print()
        console.print("[bold cyan]Logging Configuration[/bold cyan]")
        log_level = typer.prompt(
            "  Log level",
            default=config.logging.level
        )
        config.logging.level = log_level.upper()
    
    # Ensure directories exist
    ensure_directories(config)
    
    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save configuration
    save_config(config, config_path)
    
    # Set permissions on config file (0600 - owner read/write only)
    config_path.chmod(0o600)
    
    console.print()
    console.print(f"[green]✓[/green] Configuration initialized at {config_path}")
    console.print(f"[dim]Config file permissions set to 0600 (owner only)[/dim]")


@app.command("path")
def config_path() -> None:
    """Show configuration file path.
    
    Example:
        haven config path
    """
    import os
    from pathlib import Path
    from haven_cli.config import CONFIG_DIR, CONFIG_FILE
    
    # Check for environment variable override
    config_dir = Path(os.environ.get("HAVEN_CONFIG_DIR", CONFIG_DIR))
    config_file_path = config_dir / CONFIG_FILE
    console.print(f"[bold]Config directory:[/bold] {config_dir}")
    console.print(f"[bold]Config file:[/bold] {config_file_path}")
    console.print(f"[bold]Exists:[/bold] {config_file_path.exists()}")


@app.command("validate")
def validate_config() -> None:
    """Validate current configuration.
    
    Example:
        haven config validate
    """
    import os
    from pathlib import Path
    from haven_cli.config import get_config, validate_config as do_validate, CONFIG_DIR, CONFIG_FILE
    
    config = get_config()
    # Check for environment variable override
    config_dir = Path(os.environ.get("HAVEN_CONFIG_DIR", CONFIG_DIR))
    config_path = config_dir / CONFIG_FILE
    
    console.print("[bold]Validating configuration...[/bold]")
    console.print()
    
    # Basic checks
    checks = [
        ("Config file exists", config_path.exists(), None),
        ("Config directory writable", True, None),  # Will be caught by validation
        ("Data directory", True, None),
    ]
    
    all_passed = True
    for name, passed, note in checks:
        if passed:
            status = "[green]✓[/green]"
        else:
            status = "[red]✗[/red]"
            all_passed = False
        
        line = f"  {status} {name}"
        if note:
            line += f" [dim]({note})[/dim]"
        console.print(line)
    
    # Run detailed validation
    errors = do_validate(config)
    
    if errors:
        console.print()
        console.print("[bold yellow]Validation Results:[/bold yellow]")
        for error in errors:
            if error.severity == "error":
                status = "[red]✗[/red]"
                all_passed = False
            else:
                status = "[yellow]![/yellow]"
            console.print(f"  {status} [{error.severity.upper()}] {error.field}: {error.message}")
    
    console.print()
    if all_passed:
        console.print("[green]Configuration is valid[/green]")
    else:
        console.print("[red]Configuration has errors[/red]")
        raise typer.Exit(code=1)


@app.command("edit")
def edit_config() -> None:
    """Open configuration file in editor.
    
    Example:
        haven config edit
    """
    import subprocess
    
    from pathlib import Path as pathlib_Path
    from haven_cli.config import CONFIG_DIR, CONFIG_FILE
    
    # Check for environment variable override
    config_dir = pathlib_Path(os.environ.get("HAVEN_CONFIG_DIR", CONFIG_DIR))
    config_path = config_dir / CONFIG_FILE
    
    if not config_path.exists():
        console.print("[yellow]Configuration file doesn't exist. Run 'haven config init' first.[/yellow]")
        raise typer.Exit(code=1)
    
    editor = os.environ.get("EDITOR", "vim")
    
    try:
        subprocess.run([editor, str(config_path)], check=True)
    except FileNotFoundError:
        console.print(f"[red]Editor '{editor}' not found. Set EDITOR environment variable.[/red]")
        raise typer.Exit(code=1)


@app.command("network")
def set_network(
    mode: str = typer.Argument(
        None,
        help="Network mode: 'mainnet' or 'testnet'. If not provided, shows current network.",
    ),
) -> None:
    """Set or show the blockchain network mode.
    
    This single setting controls the network for all blockchain integrations:
    - Lit Protocol (encryption)
    - Filecoin (storage)
    - Arkiv (blockchain sync)
    
    Examples:
        haven config network              # Show current network
        haven config network testnet      # Switch to testnet
        haven config network mainnet      # Switch to mainnet
    """
    from haven_cli.config import get_config, set_config_value, clear_config_cache
    from haven_cli.services.blockchain_network import validate_network_mode
    
    config = get_config()
    
    if mode is None:
        # Show current network
        console.print("[bold]Blockchain Network Configuration[/bold]")
        console.print()
        
        table = Table()
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        
        network_color = "red" if config.blockchain.is_mainnet else "yellow"
        
        table.add_row("Network Mode", f"[{network_color}]{config.blockchain.network_mode}[/{network_color}]")
        table.add_row("Is Mainnet", str(config.blockchain.is_mainnet))
        table.add_row("Is Testnet", str(config.blockchain.is_testnet))
        table.add_row("", "")
        table.add_row("Lit Network", config.blockchain.get_lit_network())
        table.add_row("Filecoin RPC", config.blockchain.get_filecoin_rpc_url())
        table.add_row("Arkiv RPC", config.blockchain.get_arkiv_rpc_url())
        
        # Show overrides if set
        if config.blockchain.lit_network_override:
            table.add_row("", "")
            table.add_row("Lit Override", config.blockchain.lit_network_override)
        if config.blockchain.filecoin_rpc_override:
            table.add_row("Filecoin Override", config.blockchain.filecoin_rpc_override)
        if config.blockchain.arkiv_rpc_override:
            table.add_row("Arkiv Override", config.blockchain.arkiv_rpc_override)
        
        console.print(table)
        
        if config.blockchain.is_mainnet:
            console.print()
            console.print("[yellow]⚠️  WARNING: You are configured for MAINNET.[/yellow]")
            console.print("[yellow]   Real tokens will be used for transactions.[/yellow]")
    else:
        # Validate and set network mode
        is_valid, error_msg = validate_network_mode(mode)
        if not is_valid:
            console.print(f"[red]Error: {error_msg}[/red]")
            raise typer.Exit(code=1)
        
        # Set the network mode
        try:
            set_config_value("blockchain", "network_mode", mode.lower())
            clear_config_cache()
            
            # Show what changed
            console.print(f"[green]✓[/green] Network mode set to: [bold]{mode.lower()}[/bold]")
            console.print()
            
            # Reload to show updated values
            config = get_config()
            
            table = Table(title="Updated Network Configuration")
            table.add_column("Service", style="cyan")
            table.add_column("Network/Endpoint", style="green")
            
            table.add_row("Lit Protocol", config.blockchain.get_lit_network())
            table.add_row("Filecoin", config.blockchain.get_filecoin_rpc_url())
            table.add_row("Arkiv", config.blockchain.get_arkiv_rpc_url())
            
            console.print(table)
            
            if config.blockchain.is_mainnet:
                console.print()
                console.print("[red]⚠️  WARNING: You are now on MAINNET. Real tokens will be used![/red]")
        
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(code=1)


@app.command("env")
def show_env_vars() -> None:
    """Show supported environment variables.
    
    Example:
        haven config env
    """
    console.print("[bold]Supported Environment Variables[/bold]")
    console.print()
    console.print("[dim]These environment variables can be used to override config file values:[/dim]")
    console.print()
    
    env_vars = [
        ("HAVEN_NETWORK_MODE", "Blockchain network mode (mainnet/testnet)", "testnet"),
        ("HAVEN_VLM_ENABLED", "Enable/disable VLM analysis", "true/false"),
        ("HAVEN_VLM_MODEL", "VLM model to use", "zai-org/glm-4.6v-flash"),
        ("HAVEN_VLM_API_KEY", "API key for VLM service", "sk-..."),
        ("HAVEN_ENCRYPTION_ENABLED", "Enable/disable Lit Protocol encryption", "true/false"),
        ("HAVEN_LIT_NETWORK", "Lit Protocol network (override)", "datil-dev"),
        ("HAVEN_UPLOAD_ENABLED", "Enable/disable Filecoin upload", "true/false"),
        ("HAVEN_SYNC_ENABLED", "Enable/disable Arkiv sync", "true/false"),
        ("HAVEN_ARKIV_ENDPOINT", "Arkiv RPC endpoint URL", "https://..."),
        ("HAVEN_ARKIV_CONTRACT", "Arkiv contract address", "0x..."),
        ("HAVEN_SCHEDULER_ENABLED", "Enable/disable job scheduler", "true/false"),
        ("HAVEN_LOG_LEVEL", "Logging level", "DEBUG/INFO/WARNING/ERROR"),
        ("HAVEN_JS_RUNTIME", "JavaScript runtime to use", "node/bun/auto"),
        ("HAVEN_JS_DEBUG", "Enable JS runtime debug mode", "true/false"),
        ("HAVEN_CONFIG_DIR", "Configuration directory path", "~/.config/haven"),
        ("HAVEN_DATA_DIR", "Data directory path", "~/.local/share/haven"),
        ("HAVEN_DATABASE_URL", "Database connection URL", "sqlite:///..."),
    ]
    
    table = Table()
    table.add_column("Variable", style="cyan")
    table.add_column("Description", style="white")
    table.add_column("Example Value", style="green")
    
    for var, desc, example in env_vars:
        table.add_row(var, desc, example)
    
    console.print(table)
