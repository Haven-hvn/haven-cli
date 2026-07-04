"""Haven upload command - Upload file to Filecoin."""

import logging
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from haven_cli.services.evm_utils import normalize_haven_aol_chain, SUPPORTED_HAVEN_AOL_CHAINS

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="""Upload files to Filecoin network.
    
Creates Arkiv entities with standardized data format compatible with
haven-player (Gold Standard) and haven-dapp.

Key Fields:
  • filecoin_root_cid - CID on Filecoin (private payload)
  • is_encrypted - Encryption status
  • cid_hash - SHA256 hash for duplicate detection
  • vlm_json_cid - VLM analysis CID

Haven-AOL gated access (when --encrypt is enabled):
  • --evm-chain selects the canister-supported EVM chain
  • --access-pattern chooses gating mode (token_gated, nft_gated, owner_only, public)
  • Pattern-specific flags configure token/NFT/owner requirements

Required combinations:
  • token_gated: --token-contract --min-balance --token-standard
  • nft_gated: --nft-contract
  • owner_only: --token-contract --owner-wallet
  • public: no extra gate fields

For format details: haven-cli/docs/ARKIV_FORMAT.md
""",
    no_args_is_help=True,
)
console = Console()


@app.command(name="file")
def upload(
    file_path: Path = typer.Argument(
        ...,
        help="Path to the file to upload.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    encrypt: bool = typer.Option(
        False,
        "--encrypt",
        "-e",
        help="Encrypt file with Haven-AOL before upload.",
    ),
    skip_vlm: bool = typer.Option(
        False,
        "--no-vlm",
        help="Skip VLM analysis step.",
    ),
    dataset_id: Optional[int] = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Dataset ID for Filecoin upload.",
    ),
    skip_arkiv: bool = typer.Option(
        False,
        "--no-arkiv",
        help="Skip Arkiv blockchain sync.",
    ),
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
    ),
    title: Optional[str] = typer.Option(
        None,
        "--title",
        "-t",
        help="Video title (defaults to filename).",
    ),
    creator: Optional[str] = typer.Option(
        None,
        "--creator",
        help="Creator handle/channel identifier (e.g., @username).",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        help="Original source URL for provenance tracking.",
    ),
    access_pattern: Optional[str] = typer.Option(
        None,
        "--access-pattern",
        help=(
            "Haven-AOL access pattern for encrypted uploads. "
            "Options: token_gated, nft_gated, owner_only, public."
        ),
    ),
    token_contract: Optional[str] = typer.Option(
        None,
        "--token-contract",
        help=(
            "Token contract address used for token_gated and owner_only patterns. "
            "Required when access pattern is token_gated or owner_only."
        ),
    ),
    min_balance: Optional[str] = typer.Option(
        None,
        "--min-balance",
        help=(
            "Minimum token balance threshold in smallest token unit. "
            "Required for token_gated pattern."
        ),
    ),
    token_standard: Optional[str] = typer.Option(
        None,
        "--token-standard",
        help="Token standard for token_gated pattern. Required: ERC20 or ERC721.",
    ),
    owner_wallet: Optional[str] = typer.Option(
        None,
        "--owner-wallet",
        help="Wallet allowed by owner_only pattern. Required for owner_only.",
    ),
    nft_contract: Optional[str] = typer.Option(
        None,
        "--nft-contract",
        help="NFT contract address for nft_gated pattern. Required for nft_gated.",
    ),
    evm_chain: Optional[str] = typer.Option(
        None,
        "--evm-chain",
        help=(
            "EVM chain where access-control assets live (token/NFT for gates). "
            "Required for encrypted uploads. "
            "Supported: EthMainnet, EthSepolia, ArbitrumOne, BaseMainnet, OptimismMainnet."
        ),
    ),
    encryption_version: Optional[int] = typer.Option(
        None,
        "--encryption-version",
        help=(
            "Encryption algorithm version. "
            "1 = AES key per file (default), 3 = AES key per epoch (reduces canister calls)."
        ),
    ),
    no_dedup: bool = typer.Option(
        False,
        "--no-dedup",
        "--force",
        help=(
            "Bypass Tier 1 pre-upload deduplication. By default, if "
            "sha256(file) matches an existing catalog row, the encrypt/"
            "upload/sync steps are skipped to avoid re-uploading content "
            "this node has already archived. Use this flag to intentionally "
            "re-upload despite a local match."
        ),
    ),
) -> None:
    """Upload a file to Filecoin network.
    
    This command processes a single file through the pipeline:
    1. Ingest - Calculate pHash, create database entry
    2. Analyze - VLM analysis (optional, skip with --no-vlm)
    3. Encrypt - Haven-AOL encryption (optional, enable with --encrypt)
    4. Upload - Upload to Filecoin network
    5. Sync - Sync metadata to Arkiv blockchain (optional, skip with --no-arkiv)

    When encryption is enabled, you must provide gate parameters:
    - --evm-chain (or pipeline.evm_chain): EVM chain for access-control assets
    - --access-pattern (or pipeline.access_pattern)
    - pattern-specific gate fields (see --help option descriptions)
    
    The created Arkiv entity uses the Haven Cross-Application Data Format v1.0.0,
    ensuring compatibility with haven-player (Gold Standard) and haven-dapp.
    
    Key entity fields:
    • filecoin_root_cid - CID of video on Filecoin (private payload)
    • is_encrypted - Encryption status (boolean in payload, 0/1 in attributes)
    • cid_hash - SHA256 hash for duplicate detection (payload & attributes)
    • vlm_json_cid - CID of VLM analysis JSON (private payload)
    • encryption_metadata - encryption metadata (private payload)
    
    Example:
        haven upload file video.mp4
        haven upload file video.mp4 --encrypt --dataset 123 --evm-chain EthMainnet --access-pattern public
        haven upload file video.mp4 --encrypt --evm-chain EthMainnet --access-pattern token_gated --token-contract 0x... --min-balance 1000000 --token-standard ERC20
        haven upload file video.mp4 --encrypt --evm-chain BaseMainnet --access-pattern owner_only --token-contract 0x... --owner-wallet 0x...
        haven upload file video.mp4 --no-vlm --no-arkiv
        haven upload file video.mp4 --title "My Video" --creator "@user" --source "https://example.com"
    """
    import asyncio

    from haven_cli.config import load_config
    from haven_cli.pipeline.context import PipelineContext
    from haven_cli.pipeline.manager import create_default_pipeline

    config = load_config(config_file)
    
    console.print(f"[bold]Uploading:[/bold] {file_path.name}")
    
    # Get pipeline config values (PipelineConfig object uses attributes)
    pipeline_config = config.pipeline if config else None
    
    # Build pipeline options - CLI flags override config file settings
    # For conditional steps, we check both CLI flags and config settings
    def get_config_value(name, default):
        if pipeline_config is None:
            return default
        return getattr(pipeline_config, name, default)
    
    vlm_enabled = get_config_value("vlm_enabled", False) and not skip_vlm
    encryption_enabled = get_config_value("encryption_enabled", False) or encrypt
    upload_enabled = get_config_value("upload_enabled", True)
    sync_enabled = get_config_value("sync_enabled", False) and not skip_arkiv
    cleanup_enabled = get_config_value("cleanup_enabled", False)
    
    configured_chain = evm_chain or get_config_value("evm_chain", None)
    normalized_evm_chain: Optional[str] = None
    configured_access_pattern = access_pattern or get_config_value("access_pattern", None)
    configured_token_contract = token_contract or get_config_value("token_contract", None)
    configured_min_balance = min_balance or get_config_value("min_balance", None)
    configured_token_standard = token_standard or get_config_value("token_standard", None)
    configured_owner_wallet = owner_wallet or get_config_value("owner_wallet", None)
    configured_nft_contract = nft_contract or get_config_value("nft_contract", None)
    configured_encryption_version = encryption_version or get_config_value("encryption_version", 1)
    if configured_encryption_version not in (1, 3):
        console.print(
            f"[red]✗[/red] Unsupported encryption version {configured_encryption_version!r}. "
            "Supported: 1 (per-file) or 3 (per-epoch)."
        )
        raise typer.Exit(code=1)
    if encryption_enabled:
        icp_identity_path = os.environ.get("HAVEN_ICP_IDENTITY_PEM_PATH", "").strip()
        if not icp_identity_path:
            console.print(
                "[red]✗[/red] HAVEN_ICP_IDENTITY_PEM_PATH is required for Haven-AOL ICP encryption."
            )
            raise typer.Exit(code=1)
        if configured_chain is None:
            choices = ", ".join(SUPPORTED_HAVEN_AOL_CHAINS)
            console.print(
                "[red]✗[/red] Missing access-control asset chain for encryption. "
                f"Set --evm-chain or pipeline.evm_chain. Supported: {choices}"
            )
            raise typer.Exit(code=1)
        try:
            normalized_evm_chain = normalize_haven_aol_chain(configured_chain)
        except ValueError as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(code=1)
        if configured_access_pattern is None:
            console.print(
                "[red]✗[/red] Missing access pattern for encryption. "
                "Set --access-pattern or pipeline.access_pattern."
            )
            raise typer.Exit(code=1)
        valid_patterns = {"token_gated", "nft_gated", "owner_only", "public"}
        normalized_pattern = configured_access_pattern.strip().lower()
        if normalized_pattern not in valid_patterns:
            console.print(
                f"[red]✗[/red] Unsupported access pattern {configured_access_pattern!r}. "
                "Use token_gated, nft_gated, owner_only, or public."
            )
            raise typer.Exit(code=1)
        if normalized_pattern in {"token_gated", "owner_only"} and not configured_token_contract:
            console.print(
                "[red]✗[/red] token_contract is required for token_gated/owner_only patterns."
            )
            raise typer.Exit(code=1)
        if normalized_pattern == "token_gated":
            if configured_min_balance is None:
                console.print("[red]✗[/red] min_balance is required for token_gated pattern.")
                raise typer.Exit(code=1)
            if configured_token_standard is None:
                console.print("[red]✗[/red] token_standard is required for token_gated pattern.")
                raise typer.Exit(code=1)
        if normalized_pattern == "owner_only" and not configured_owner_wallet:
            console.print("[red]✗[/red] owner_wallet is required for owner_only pattern.")
            raise typer.Exit(code=1)
        if normalized_pattern == "nft_gated" and not configured_nft_contract:
            console.print("[red]✗[/red] nft_contract is required for nft_gated pattern.")
            raise typer.Exit(code=1)
    else:
        normalized_pattern = configured_access_pattern.strip().lower() if configured_access_pattern else None

    options = {
        "encrypt": encryption_enabled,
        "vlm_enabled": vlm_enabled,
        "upload_enabled": upload_enabled,
        "arkiv_sync_enabled": sync_enabled,
        "cleanup_enabled": cleanup_enabled,
        # Tier 1 pre-upload deduplication. Default is enabled; --no-dedup
        # / --force bypasses it. See docs/BATCH_SYNC_TIER1_PREUPLOAD_DEDUP.md.
        "dedup_enabled": not no_dedup,
        "dataset_id": dataset_id,
        "title": title,
        "creator_handle": creator,
        "source_uri": source,
        "evm_chain": normalized_evm_chain,
        "access_pattern": normalized_pattern,
        "token_contract": configured_token_contract,
        "min_balance": configured_min_balance,
        "token_standard": configured_token_standard,
        "owner_wallet": configured_owner_wallet,
        "nft_contract": configured_nft_contract,
        "encryption_version": configured_encryption_version,
    }
    
    # Create pipeline context
    context = PipelineContext(
        source_path=file_path,
        options=options,
    )
    
    # Phase 2 (BATCH_SYNC_REMEDIATION_PLAN.md): one-shot `haven upload file`
    # always uses the default pipeline. The previous implementation routed
    # single-file CLI uploads through `create_batched_pipeline` whenever
    # `batch_sync_enabled=true`, which meant:
    #   1. We constructed a BatchAccumulator + FlushQueue for one item
    #      (no amortization benefit).
    #   2. The flush path landed on `batch_sync_contexts([ctx])`, which
    #      historically skipped the `find_existing_entity()` dedup that
    #      `sync_context()` performs — so re-uploading the same file
    #      produced a duplicate Arkiv entity.
    # The default pipeline's SyncStep calls `sync_context()` directly,
    # which has dedup parity. `batch_sync_enabled` now applies only to
    # the daemon and scheduled jobs, not interactive `haven upload`.
    pipeline_manager = create_default_pipeline(config=config.__dict__ if config else None)
    
    async def run_pipeline() -> None:
        from haven_cli.js_runtime.manager import JSBridgeManager
        
        debug_mode = (
            os.environ.get("DEBUG") == "1"
            or os.environ.get("LOG_LEVEL", "").lower() == "debug"
        )
        JSBridgeManager.get_instance().configure(
            network_mode=config.blockchain.effective_filecoin_network_mode,
            debug=debug_mode,
        )
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Processing...", total=None)
            
            try:
                # Process through pipeline (SyncStep handles N=1 sync via
                # ArkivSyncClient.sync_context, which does dedup).
                result = await pipeline_manager.process(context)
                
                progress.update(task, completed=True)
                
                if result.success:
                    console.print(f"[green]✓[/green] Upload complete: {result.cid or 'N/A'}")
                else:
                    console.print(f"[red]✗[/red] Upload failed: {result.error}")
                    raise typer.Exit(code=1)
            finally:
                # CRITICAL: Shutdown JS Bridge Manager to prevent hang.
                # The background health check task keeps the event loop alive.
                logger.debug("Shutting down JS Bridge Manager...")
                await JSBridgeManager.get_instance().shutdown()
    
    # Run the async pipeline
    asyncio.run(run_pipeline())
