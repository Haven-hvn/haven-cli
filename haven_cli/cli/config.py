"""Haven config command - Configuration management."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax

from haven_cli.access_pattern import parse_access_pattern_choice
from haven_cli.bittorrent_plugin_init import BitTorrentJobInitSpec
from haven_cli.services.evm_utils import SUPPORTED_HAVEN_AOL_CHAINS

app = typer.Typer(help="Manage Haven configuration.")
console = Console()


def _prompt_mainnet_or_testnet(*, title: str, blurb: str) -> str:
    """Return ``mainnet`` or ``testnet`` after interactive prompts."""
    console.print(f"  [bold]{title}[/bold]")
    console.print(f"  [dim]{blurb}[/dim]")
    console.print("  [1] Testnet (recommended for development)")
    console.print("  [2] Mainnet (production; real tokens)")
    choice = typer.prompt("  Select option", default="1", show_choices=False)
    if choice == "2":
        if typer.confirm(
            "  [red]Mainnet uses real tokens. Confirm?[/red]",
            default=False,
        ):
            return "mainnet"
        return "testnet"
    return "testnet"


_ACCESS_CONTROL_CHAIN_BLURB: dict[str, str] = {
    "EthMainnet": "Ethereum mainnet",
    "EthSepolia": "Ethereum Sepolia (testnet)",
    "ArbitrumOne": "Arbitrum One",
    "BaseMainnet": "Base",
    "OptimismMainnet": "Optimism",
}


def _prompt_access_control_asset_chain() -> str:
    """Interactive menu for ``pipeline.evm_chain`` (gate asset chain)."""
    console.print("  [bold]Access-control asset chain (EVM)[/bold]")
    console.print(
        "  [dim]Haven-AOL runs on Internet Computer mainnet. Choose the EVM chain where[/dim]"
    )
    console.print(
        "  [dim]your token/NFT lives—access rules are enforced against that chain.[/dim]"
    )
    for idx, chain in enumerate(SUPPORTED_HAVEN_AOL_CHAINS, start=1):
        blurb = _ACCESS_CONTROL_CHAIN_BLURB.get(chain, chain)
        console.print(f"  [{idx}] {chain} — {blurb}")
    default_n = "2" if "EthSepolia" in SUPPORTED_HAVEN_AOL_CHAINS else "1"
    while True:
        choice = typer.prompt(
            "  Select access-control chain",
            default=default_n,
            show_choices=False,
        )
        try:
            n = int(choice.strip())
        except ValueError:
            console.print("  [red]Enter a number from the list.[/red]")
            continue
        if 1 <= n <= len(SUPPORTED_HAVEN_AOL_CHAINS):
            return SUPPORTED_HAVEN_AOL_CHAINS[n - 1]
        console.print("  [red]Enter a number from the list.[/red]")


_EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _prompt_nonempty_evm_address(label: str, *, default: str = "") -> str:
    """Prompt until the user enters a valid 20-byte hex EVM address."""
    while True:
        raw = typer.prompt(
            label,
            default=default,
            show_default=bool(default),
        ).strip()
        if _EVM_ADDR_RE.match(raw):
            return raw
        console.print("  [red]Enter a 0x-prefixed 40-hex-character address.[/red]")


def _prompt_token_standard() -> str:
    console.print("  [1] ERC20 (fungible — threshold is smallest units, e.g. wei)")
    console.print("  [2] ERC721 (NFT — threshold is minimum count, usually 1)")
    while True:
        choice = typer.prompt("  Token standard", default="1", show_choices=False).strip()
        if choice == "1" or choice.upper() == "ERC20":
            return "ERC20"
        if choice == "2" or choice.upper() == "ERC721":
            return "ERC721"
        console.print("  [red]Enter 1, 2, ERC20, or ERC721.[/red]")


def _prompt_encryption_access_gate(config: Any) -> None:
    """Collect ``access_pattern`` and gate fields for ``config.pipeline``."""
    console.print()
    console.print("  [bold]Access pattern[/bold]")
    console.print(
        "  [dim]Who may decrypt: pick the rule and the on-chain asset used to enforce it.[/dim]"
    )
    console.print("  [1] public — policy does not require a token/NFT balance")
    console.print("  [2] token_gated — require at least a minimum balance of a token")
    console.print("  [3] nft_gated — require holding an NFT from a collection")
    console.print("  [4] owner_only — only a specific wallet, using a token contract gate")
    while True:
        raw = typer.prompt("  Select pattern", default="2", show_choices=False)
        try:
            pattern = parse_access_pattern_choice(raw)
            break
        except ValueError:
            console.print(
                "  [red]Enter 1–4, or one of: public, token_gated, nft_gated, owner_only.[/red]"
            )

    config.pipeline.access_pattern = pattern
    # Clear fields that may have been set in a prior partial run (init uses fresh config)
    config.pipeline.token_contract = None
    config.pipeline.min_balance = None
    config.pipeline.token_standard = None
    config.pipeline.owner_wallet = None
    config.pipeline.nft_contract = None

    if pattern == "public":
        console.print(
            "  [dim]public: no token contract stored. "
            "Haven-AOL encryption still expects gate details when you run uploads; "
            "use token_gated or set pipeline fields before encrypting if uploads fail.[/dim]"
        )
        console.print(f"  [green]✓[/green] access_pattern = {pattern}")
        return

    if pattern == "token_gated":
        config.pipeline.token_contract = _prompt_nonempty_evm_address(
            "  ERC-20/721 contract address (the asset balances are checked against)",
        )
        config.pipeline.min_balance = typer.prompt(
            "  Minimum balance (integer in smallest units, e.g. wei for ERC-20, or count for ERC-721)",
            default="1",
        ).strip()
        config.pipeline.token_standard = _prompt_token_standard()
    elif pattern == "nft_gated":
        config.pipeline.nft_contract = _prompt_nonempty_evm_address(
            "  NFT collection contract address",
        )
    elif pattern == "owner_only":
        config.pipeline.token_contract = _prompt_nonempty_evm_address(
            "  Token contract address (used for the gate)",
        )
        config.pipeline.owner_wallet = _prompt_nonempty_evm_address(
            "  Owner wallet address (only this wallet satisfies the gate)",
        )

    console.print(f"  [green]✓[/green] access_pattern = {pattern}")
    if config.pipeline.token_contract:
        console.print(f"  [dim]token_contract[/dim] {config.pipeline.token_contract}")
    if config.pipeline.min_balance:
        console.print(f"  [dim]min_balance[/dim] {config.pipeline.min_balance}")
    if config.pipeline.token_standard:
        console.print(f"  [dim]token_standard[/dim] {config.pipeline.token_standard}")
    if config.pipeline.owner_wallet:
        console.print(f"  [dim]owner_wallet[/dim] {config.pipeline.owner_wallet}")
    if config.pipeline.nft_contract:
        console.print(f"  [dim]nft_contract[/dim] {config.pipeline.nft_contract}")


def _prompt_bittorrent_plugin(config: Any) -> Optional[BitTorrentJobInitSpec]:
    """Interactive BitTorrent plugin section for ``haven config init``.

    Returns:
        Job spec if the user opts in to creating a recurring poll job, else ``None``.
    """
    from haven_cli.bittorrent_plugin_init import (
        build_bittorrent_plugin_settings,
        sync_bittorrent_plugin_lists,
    )

    job_spec: Optional[BitTorrentJobInitSpec] = None

    console.print()
    console.print("[bold cyan]BitTorrent plugin[/bold cyan]")
    console.print(
        "  [dim]Scheduled jobs can scrape forum magnet links and download via libtorrent.[/dim]"
    )
    bt_on = typer.confirm(
        "  Enable BitTorrent plugin?",
        default=True,
    )

    download_default = str(config.data_dir / "bittorrent")
    dl_dir = "downloads/bittorrent"
    max_cd = 3
    sources: List[Dict[str, Any]] = []

    if bt_on:
        dl_dir = typer.prompt(
            "  Download directory",
            default=download_default,
        ).strip() or download_default
        max_cd = int(
            typer.prompt(
                "  Max concurrent torrent downloads",
                default="3",
            )
        )
        add_forum = typer.confirm(
            "  Add a forum magnet source now?",
            default=False,
        )
        if add_forum:
            name = (
                typer.prompt("  Source name (identifier)", default="primary_forum").strip()
                or "primary_forum"
            )
            domain = typer.prompt(
                "  Forum domain (hostname only, e.g. example.com)",
                default="",
            ).strip()
            if domain:
                forum_id = (
                    typer.prompt("  Forum ID (fid query parameter)", default="1").strip()
                    or "1"
                )
                max_threads = int(
                    typer.prompt(
                        "  Max threads to scan from the forum listing",
                        default="10",
                    )
                )
                sources.append(
                    {
                        "name": name,
                        "type": "forum",
                        "domain": domain,
                        "forum_id": forum_id,
                        "max_threads": max_threads,
                        "enabled": True,
                    }
                )
            else:
                console.print(
                    "  [yellow]No domain entered — skip forum source "
                    "(edit [plugins.settings.bittorrent] later).[/yellow]"
                )

    settings = build_bittorrent_plugin_settings(
        enabled=bt_on,
        download_dir=dl_dir,
        max_concurrent_downloads=max_cd,
        sources=sources if bt_on else None,
    )
    config.plugins.plugin_settings["bittorrent"] = settings
    sync_bittorrent_plugin_lists(
        config.plugins.enabled_plugins,
        config.plugins.disabled_plugins,
        bittorrent_enabled=bt_on,
    )
    status = "enabled" if bt_on else "disabled"
    console.print(f"  [green]✓[/green] BitTorrent plugin {status}")

    if bt_on and config.scheduler.enabled:
        default_job = bool(sources)
        if typer.confirm(
            "  Create a scheduled job to poll BitTorrent sources (cron)?",
            default=default_job,
        ):
            while True:
                schedule = typer.prompt(
                    "  Cron schedule",
                    default=config.scheduler.default_cron,
                ).strip()
                try:
                    from croniter import croniter

                    croniter(schedule)
                    break
                except ValueError as exc:
                    console.print(f"[red]Invalid cron: {exc}[/red]")
            job_spec = BitTorrentJobInitSpec(schedule=schedule, on_success="archive_new")
    elif bt_on and not config.scheduler.enabled:
        console.print(
            "  [dim]Scheduler is disabled — enable [scheduler.enabled] and run "
            "`haven jobs create --plugin bittorrent` to poll on a schedule.[/dim]"
        )

    return job_spec


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
            ("filecoin_network_mode", config.blockchain.filecoin_network_mode or "(inherit)", False),
            ("arkiv_network_mode", config.blockchain.arkiv_network_mode or "(inherit)", False),
            ("effective_filecoin_network", config.blockchain.effective_filecoin_network_mode, False),
            ("effective_arkiv_network", config.blockchain.effective_arkiv_network_mode, False),
            ("is_mainnet", str(config.blockchain.is_mainnet), False),
            ("filecoin_rpc_url", config.blockchain.get_filecoin_rpc_url(), False),
            ("arkiv_rpc_url", config.blockchain.get_arkiv_rpc_url(), False),
            ("filecoin_rpc_override", config.blockchain.filecoin_rpc_override or "", False),
            ("arkiv_rpc_override", config.blockchain.arkiv_rpc_override or "", False),
        ],
        "pipeline": [
            ("vlm_enabled", str(config.pipeline.vlm_enabled), False),
            ("vlm_model", config.pipeline.vlm_model, False),
            ("vlm_api_key", mask(config.pipeline.vlm_api_key or "", True), True),
            ("vlm_timeout", str(config.pipeline.vlm_timeout), False),
            ("encryption_enabled", str(config.pipeline.encryption_enabled), False),
            (
                "evm_chain (access-control asset)",
                config.pipeline.evm_chain or "",
                False,
            ),
            ("access_pattern", config.pipeline.access_pattern or "", False),
            ("token_contract", config.pipeline.token_contract or "", False),
            ("min_balance", config.pipeline.min_balance or "", False),
            ("token_standard", config.pipeline.token_standard or "", False),
            ("owner_wallet", config.pipeline.owner_wallet or "", False),
            ("nft_contract", config.pipeline.nft_contract or "", False),
            ("upload_enabled", str(config.pipeline.upload_enabled), False),
            ("sync_enabled", str(config.pipeline.sync_enabled), False),
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
    
    config_existed = config_path.exists()
    
    if config_existed and not force:
        console.print(f"[yellow]Configuration already exists at {config_path}[/yellow]")
        console.print("[dim]Using existing configuration (use --force to overwrite)[/dim]")
        console.print()
        
        # Still ensure directories and database are set up
        from haven_cli.config import get_config, ensure_directories
        from haven_cli.database.connection import create_tables
        
        config = get_config()
        ensure_directories(config)
        create_tables(config)
        
        console.print(f"[green]✓[/green] Directories verified")
        console.print(f"[green]✓[/green] Database tables ready")
        return
    
    console.print("[bold]Initializing Haven configuration...[/bold]")
    console.print()
    
    # Create default config
    config = HavenConfig()
    config.config_dir = config_dir
    config.data_dir = Path(os.environ.get("HAVEN_DATA_DIR", config_dir.parent.parent / ".local" / "share" / "haven"))
    
    bt_job_spec: Optional[BitTorrentJobInitSpec] = None
    if interactive:
        # Interactive wizard — Filecoin / Arkiv networks vs EVM chain for gate assets
        console.print("[bold cyan]Filecoin (storage) network[/bold cyan]")
        filecoin_mode = _prompt_mainnet_or_testnet(
            title="Filecoin network",
            blurb="Synapse uploads use this network (mainnet vs calibration testnet).",
        )
        config.blockchain.filecoin_network_mode = filecoin_mode
        config.blockchain.network_mode = filecoin_mode
        console.print(f"  [dim]Filecoin RPC: {config.blockchain.get_filecoin_rpc_url()}[/dim]")
        filecoin_endpoint = typer.prompt(
            "  Filecoin RPC URL override (Enter to use default)",
            default="",
        )
        if filecoin_endpoint.strip():
            config.blockchain.filecoin_rpc_override = filecoin_endpoint.strip()
        console.print(f"  [green]✓[/green] Filecoin: {config.blockchain.effective_filecoin_network_mode}")
        console.print()
        
        console.print("[bold cyan]Arkiv (sync) network[/bold cyan]")
        arkiv_enabled = typer.confirm("  Enable Arkiv sync?", default=config.pipeline.sync_enabled)
        config.pipeline.sync_enabled = arkiv_enabled
        if arkiv_enabled:
            arkiv_mode = _prompt_mainnet_or_testnet(
                title="Arkiv network",
                blurb="Can differ from Filecoin (e.g. testnet Arkiv with mainnet token gates).",
            )
            config.blockchain.arkiv_network_mode = arkiv_mode
            console.print(f"  [dim]Arkiv RPC: {config.blockchain.get_arkiv_rpc_url()}[/dim]")
            arkiv_endpoint = typer.prompt(
                "  Arkiv RPC URL (Enter to use default)",
                default="",
            )
            if arkiv_endpoint.strip():
                config.blockchain.arkiv_rpc_override = arkiv_endpoint.strip()
            console.print(f"  [green]✓[/green] Arkiv: {config.blockchain.effective_arkiv_network_mode}")
        else:
            console.print("  [dim]Arkiv network skipped (sync disabled).[/dim]")
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
        console.print("[bold cyan]Encryption (Haven-AOL) Configuration[/bold cyan]")
        console.print(
            "  [dim]Haven-AOL is deployed on Internet Computer mainnet. If you enable encryption,[/dim]"
        )
        console.print(
            "  [dim]you must pick the EVM chain where your access-control asset (token/NFT) lives.[/dim]"
        )
        encryption_enabled = typer.confirm(
            "  Enable Haven-AOL encryption?",
            default=config.pipeline.encryption_enabled
        )
        config.pipeline.encryption_enabled = encryption_enabled
        if encryption_enabled:
            chosen_chain = _prompt_access_control_asset_chain()
            config.pipeline.evm_chain = chosen_chain
            console.print(
                f"  [green]✓[/green] Access-control asset chain: {chosen_chain} (saved as pipeline.evm_chain)"
            )
            _prompt_encryption_access_gate(config)
        else:
            console.print(
                "  [dim]Access-control chain not set (configure pipeline.evm_chain when enabling encryption).[/dim]"
            )
        
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
        
        bt_job_spec = _prompt_bittorrent_plugin(config)
        
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
    
    # Initialize database tables
    from haven_cli.database.connection import create_tables
    create_tables(config)
    
    if interactive and bt_job_spec is not None:
        from haven_cli.bittorrent_plugin_init import create_bittorrent_scheduled_job_if_absent

        try:
            bt_result = create_bittorrent_scheduled_job_if_absent(
                bt_job_spec.schedule,
                bt_job_spec.on_success,
            )
        except ValueError as exc:
            console.print(f"[yellow]Could not create BitTorrent scheduled job: {exc}[/yellow]")
        else:
            if bt_result == "created":
                console.print(
                    "[green]✓[/green] Scheduled BitTorrent polling job created (`haven jobs list`)."
                )
            else:
                console.print(
                    "[dim]BitTorrent scheduled job already exists; left unchanged.[/dim]"
                )
    
    console.print()
    console.print(f"[green]✓[/green] Configuration initialized at {config_path}")
    console.print(f"[green]✓[/green] Database tables created")
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
    """Set or show blockchain network modes (Filecoin, Arkiv, and legacy ``network_mode``).
    
    ``haven config network mainnet`` sets Filecoin, Arkiv, and ``network_mode`` together.
    Use ``haven config set`` for independent values, e.g. ``blockchain.filecoin_network_mode``.
    Encryption gates use ``pipeline.evm_chain`` for the EVM network where access
    conditions are enforced (Haven-AOL itself is on Internet Computer mainnet).
    
    Examples:
        haven config network              # Show current network
        haven config network testnet      # Switch all chain modes to testnet
        haven config network mainnet      # Switch all chain modes to mainnet
    """
    import os
    from haven_cli.config import (
        CONFIG_DIR,
        CONFIG_FILE,
        clear_config_cache,
        get_config,
        load_config,
        save_config,
    )
    from haven_cli.services.blockchain_network import validate_network_mode
    
    config_dir = Path(os.environ.get("HAVEN_CONFIG_DIR", CONFIG_DIR))
    config_path = config_dir / CONFIG_FILE
    
    config = get_config()
    
    if mode is None:
        # Show current network
        console.print("[bold]Blockchain Network Configuration[/bold]")
        console.print()
        
        table = Table()
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        
        fc_main = config.blockchain.effective_filecoin_network_mode == "mainnet"
        network_color = "red" if fc_main else "yellow"
        
        table.add_row("network_mode (legacy)", f"[{network_color}]{config.blockchain.network_mode}[/{network_color}]")
        table.add_row("Filecoin mode", config.blockchain.effective_filecoin_network_mode)
        table.add_row("Arkiv mode", config.blockchain.effective_arkiv_network_mode)
        table.add_row("Haven-AOL deployment", "Internet Computer (mainnet)")
        table.add_row(
            "Access-control asset chain (evm_chain)",
            config.pipeline.evm_chain or "(not set)",
        )
        table.add_row("", "")
        table.add_row("Filecoin RPC", config.blockchain.get_filecoin_rpc_url())
        table.add_row("Arkiv RPC", config.blockchain.get_arkiv_rpc_url())
        
        # Show overrides if set
        if config.blockchain.filecoin_rpc_override:
            table.add_row("Filecoin Override", config.blockchain.filecoin_rpc_override)
        if config.blockchain.arkiv_rpc_override:
            table.add_row("Arkiv Override", config.blockchain.arkiv_rpc_override)
        
        console.print(table)
        
        if config.blockchain.is_mainnet or (
            config.blockchain.effective_arkiv_network_mode == "mainnet"
        ):
            console.print()
            console.print("[yellow]⚠️  WARNING: Mainnet is in use for one or more services.[/yellow]")
            console.print("[yellow]   Real tokens can be spent on those networks.[/yellow]")
    else:
        # Validate and set network mode (all three fields together)
        is_valid, error_msg = validate_network_mode(mode)
        if not is_valid:
            console.print(f"[red]Error: {error_msg}[/red]")
            raise typer.Exit(code=1)
        
        try:
            merged = load_config(config_path)
            m = mode.lower()
            merged.blockchain.network_mode = m
            merged.blockchain.filecoin_network_mode = m
            merged.blockchain.arkiv_network_mode = m
            save_config(merged, config_path)
            clear_config_cache()
            
            console.print(f"[green]✓[/green] All blockchain network modes set to: [bold]{m}[/bold]")
            console.print()
            
            config = get_config()
            
            table = Table(title="Updated Network Configuration")
            table.add_column("Service", style="cyan")
            table.add_column("Mode / endpoint", style="green")
            
            table.add_row("Filecoin", config.blockchain.effective_filecoin_network_mode)
            table.add_row("Filecoin RPC", config.blockchain.get_filecoin_rpc_url())
            table.add_row("Arkiv", config.blockchain.effective_arkiv_network_mode)
            table.add_row("Arkiv RPC", config.blockchain.get_arkiv_rpc_url())
            table.add_row("Haven-AOL (ICP)", "mainnet (fixed)")
            table.add_row(
                "Gate asset chain (unchanged)",
                config.pipeline.evm_chain or "(pipeline.evm_chain)",
            )
            
            console.print(table)
            
            if m == "mainnet":
                console.print()
                console.print("[red]⚠️  WARNING: Filecoin and Arkiv are on MAINNET. Real tokens will be used![/red]")
        
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(code=1)


@app.command("setup-vlm")
def setup_vlm(
    url: str = typer.Option(
        None,
        "--url",
        "-u",
        help="VLM endpoint URL (e.g., http://localhost:1234/v1)",
    ),
    name: str = typer.Option(
        "default",
        "--name",
        "-n",
        help="Endpoint name identifier",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        "-k",
        help="API key for this endpoint",
    ),
    weight: int = typer.Option(
        1,
        "--weight",
        "-w",
        help="Load balancing weight",
    ),
    max_concurrent: int = typer.Option(
        5,
        "--max-concurrent",
        "-c",
        help="Maximum concurrent requests for this endpoint",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Interactive setup mode",
    ),
) -> None:
    """Setup VLM multiplexer configuration.
    
    This command configures VLM endpoints for load balancing across multiple servers.
    The multiplexer is now the ONLY supported method for VLM processing.
    
    Examples:
        # Quick setup with single endpoint
        haven config setup-vlm --url http://localhost:1234/v1
        
        # Setup with custom options
        haven config setup-vlm --url http://gpu-server:1234/v1 --name primary --weight 8
        
        # Interactive setup
        haven config setup-vlm --interactive
        
        # Add API key
        haven config setup-vlm --url http://api.openai.com/v1 --api-key sk-...
    """
    from haven_cli.config import get_config, save_config
    from haven_cli.vlm.config import save_multiplexer_config, get_example_multiplexer_config
    
    config = get_config()
    
    if interactive:
        console.print("[bold]VLM Multiplexer Setup (Interactive)[/bold]")
        console.print()
        
        # Get endpoint URL
        url = typer.prompt("Endpoint URL", default="http://localhost:1234/v1")
        name = typer.prompt("Endpoint name", default="default")
        weight = typer.prompt("Weight (higher = more traffic)", default=1, type=int)
        max_concurrent = typer.prompt("Max concurrent requests", default=5, type=int)
        
        # Optional API key
        api_key_input = typer.prompt("API key (optional)", default="", show_default=False)
        api_key = api_key_input if api_key_input else None
    
    if not url:
        console.print("[red]Error: --url is required (or use --interactive)[/red]")
        console.print()
        console.print("Example:")
        console.print("  haven config setup-vlm --url http://localhost:1234/v1")
        raise typer.Exit(code=1)
    
    # Create endpoint config
    endpoint = {
        "base_url": url,
        "name": name,
        "weight": weight,
        "max_concurrent": max_concurrent,
    }
    if api_key:
        endpoint["api_key"] = api_key
    
    # Enable multiplexer and save endpoint
    config.pipeline.vlm_multiplexer_enabled = True
    
    # Add to existing endpoints or create new list
    if not config.pipeline.vlm_multiplexer_endpoints:
        config.pipeline.vlm_multiplexer_endpoints = []
    
    config.pipeline.vlm_multiplexer_endpoints.append(endpoint)
    
    # Save config
    save_config(config)
    
    # Also save to separate JSON file for compatibility
    save_multiplexer_config(config.pipeline.vlm_multiplexer_endpoints)
    
    console.print(f"[green]✅ Added VLM endpoint:[/green] {name} ({url})")
    console.print()
    console.print("[bold]Current multiplexer configuration:[/bold]")
    console.print(f"  Enabled: {config.pipeline.vlm_multiplexer_enabled}")
    console.print(f"  Endpoints: {len(config.pipeline.vlm_multiplexer_endpoints)}")
    for i, ep in enumerate(config.pipeline.vlm_multiplexer_endpoints, 1):
        console.print(f"    {i}. {ep.get('name')} → {ep.get('base_url')} (weight={ep.get('weight')})")
    console.print()
    console.print("[dim]Add more endpoints by running this command again.[/dim]")


@app.command("vlm-example")
def show_vlm_example() -> None:
    """Show example VLM multiplexer configuration.
    
    Example:
        haven config vlm-example
    """
    from haven_cli.vlm.config import get_example_multiplexer_config
    
    console.print("[bold]Example VLM Multiplexer Configuration[/bold]")
    console.print()
    console.print("You can save this to your data directory as 'vlm_multiplexer.json':")
    console.print()
    
    example = get_example_multiplexer_config()
    console.print(Syntax(example, "json", theme="monokai"))
    console.print()
    console.print("Or add to your config.toml:")
    console.print()
    console.print(Syntax("""[pipeline]
vlm_multiplexer_enabled = true

[[pipeline.vlm_multiplexer_endpoints]]
base_url = "http://primary-server:1234/v1"
name = "primary"
weight = 8
max_concurrent = 10

[[pipeline.vlm_multiplexer_endpoints]]
base_url = "http://secondary-server:1234/v1"
name = "secondary"
weight = 1
max_concurrent = 5""", "toml", theme="monokai"))


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
        ("HAVEN_VLM_MULTIPLEXER_ENABLED", "Enable VLM multiplexer", "true/false"),
        ("HAVEN_PRIVATE_KEY", "Private key for Filecoin blockchain auth (REQUIRED)", "0x..."),
        ("HAVEN_ENCRYPTION_ENABLED", "Enable/disable Haven-AOL encryption", "true/false"),
        ("HAVEN_ICP_IDENTITY_PEM_PATH", "ICP user identity PEM path for Haven-AOL requests (REQUIRED for encryption/decryption)", "/path/to/identity.pem"),
        ("HAVEN_ICP_HOST", "ICP API host for Haven-AOL calls", "https://icp-api.io"),
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
