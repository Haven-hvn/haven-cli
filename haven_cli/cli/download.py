"""Haven download command - Download and decrypt files from Filecoin."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

from haven_cli.config import load_config
from haven_cli.js_runtime.manager import JSBridgeManager, js_call
from haven_cli.js_runtime.protocol import JSRuntimeMethods
from haven_cli.crypto.haven_aol_local import (
    GateParams,
    derivation_threshold_from_access_condition,
    decrypt_file_streaming,
)
from haven_cli.services.evm_utils import normalize_haven_aol_chain
from haven_cli.crypto import (
    EncryptionMetadata,
    load_encryption_metadata,
    load_encryption_metadata_by_cid,
    load_encryption_metadata_from_arkiv_entity,
    load_encryption_metadata_from_arkiv_query,
    verify_cid_format,
)

app = typer.Typer(
    help="Download and decrypt files from Filecoin network.",
    no_args_is_help=True,
)
console = Console()


@app.command(name="cid")
def download(
    cid: str = typer.Argument(
        ...,
        help="Content ID (CID) of the file to download.",
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Output path for downloaded file.",
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    decrypt: bool = typer.Option(
        False,
        "--decrypt",
        "-d",
        help="Decrypt file after download using Haven-AOL.",
    ),
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
    ),
    entity_key: Optional[str] = typer.Option(
        None,
        "--entity-key",
        help="Arkiv entity key for encryption metadata lookup (if not in local DB).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing file if it exists.",
    ),
) -> None:
    """Download a file from Filecoin network.
    
    This command retrieves a file by its CID and optionally decrypts it
    using Haven-AOL if it was encrypted during upload.
    
    The download process:
    1. Connects to the Synapse SDK
    2. Downloads the file from Filecoin to the specified output path
    3. If --decrypt is specified, decrypts using Haven-AOL
    
    Encryption metadata is looked up in the following order:
    1. Database (if CID was previously uploaded)
    2. Sidecar file (.encmeta extension)
    3. Arkiv entity (if --entity-key is provided)
    4. Arkiv query by CID hash (automatic fallback for encrypted files)
    
    Example:
        haven download bafybeig... --output video.mp4
        haven download bafybeig... --output video.mp4 --decrypt
        haven download bafybeig... --output video.mp4 --decrypt --entity-key 0x1234...
        haven download Qm... --output video.mp4 --force
    """
    config = load_config(config_file)
    
    # Strip whitespace from CID (handle potential newline issues)
    cid = cid.strip()
    
    # Validate CID format
    if not verify_cid_format(cid):
        console.print(f"[red]✗[/red] Invalid CID format: {repr(cid)}")
        console.print("[yellow]CIDs should start with 'Qm' (CIDv0) or 'baf' (CIDv1)[/yellow]")
        raise typer.Exit(code=1)
    
    # Check if output already exists
    if output.exists() and not force:
        console.print(f"[red]✗[/red] Output file already exists: {output}")
        console.print("[yellow]Use --force to overwrite[/yellow]")
        raise typer.Exit(code=1)
    
    # Ensure output directory exists
    output.parent.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[bold]Downloading:[/bold] {cid}")
    console.print(f"[bold]Output:[/bold] {output}")
    
    if decrypt:
        console.print("[bold]Decryption:[/bold] Enabled")
    
    async def run_download() -> None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Downloading...", total=100)
            
            try:
                # Use bridge manager for singleton access with retry
                manager = JSBridgeManager.get_instance()
                
                async with manager:
                    # Connect to Synapse (uses FILECOIN_RPC_URL and HAVEN_PRIVATE_KEY env vars)
                    progress.update(task, description="Connecting to Synapse...")
                    
                    await js_call(
                        JSRuntimeMethods.SYNAPSE_CONNECT,
                        {},
                        max_retries=3,
                    )
                    
                    # Download file from Filecoin
                    progress.update(task, description="Fetching from Filecoin...", advance=10)
                    
                    result = await js_call(
                        JSRuntimeMethods.SYNAPSE_DOWNLOAD,
                        {
                            "cid": cid,
                            "outputPath": str(output),
                        },
                        max_retries=3,
                    )
                    
                    progress.update(task, advance=50)
                    
                    # Decrypt if requested
                    if decrypt:
                        progress.update(task, description="Decrypting with Haven-AOL...")
                        
                        await _decrypt_file(
                            output,
                            output,
                            cid,
                            entity_key=entity_key,
                        )
                        
                        progress.update(task, description="Decryption complete")
                    
                    progress.update(task, advance=40)
                    
                    console.print(f"[green]✓[/green] Download complete: {output}")
                    
                    # Show file info
                    file_size = output.stat().st_size if output.exists() else 0
                    if file_size > 0:
                        size_str = _format_file_size(file_size)
                        console.print(f"[dim]File size: {size_str}[/dim]")
                    
            except Exception as e:
                console.print(f"[red]✗[/red] Download failed: {e}")
                
                # Clean up partial download
                if output.exists():
                    try:
                        output.unlink()
                        console.print("[yellow]Cleaned up partial download[/yellow]")
                    except OSError:
                        pass
                
                raise typer.Exit(code=1)
    
    asyncio.run(run_download())


async def _decrypt_file(
    input_path: Path,
    output_path: Path,
    cid: str,
    entity_key: Optional[str] = None,
) -> None:
    """Decrypt a file using Haven-AOL.
    
    Args:
        input_path: Path to the encrypted file
        output_path: Path to write the decrypted file
        cid: Content ID for metadata lookup
        entity_key: Optional Arkiv entity key for metadata lookup
    Raises:
        ValueError: If encryption metadata is not found
        RuntimeError: If decryption fails
    """
    # Load encryption metadata
    metadata = await load_encryption_metadata_by_cid(cid)
    
    if not metadata:
        # Try sidecar file
        metadata = await load_encryption_metadata(input_path)
    
    if not metadata and entity_key:
        # Try fetching from Arkiv entity directly
        console.print(f"[dim]Fetching encryption metadata from Arkiv entity {entity_key}...[/dim]")
        arkiv_rpc = os.environ.get("ARKIV_RPC_URL", "https://braga.hoodi.arkiv.network/rpc")
        private_key = os.environ.get("HAVEN_PRIVATE_KEY", "")
        if private_key:
            metadata = await load_encryption_metadata_from_arkiv_entity(
                entity_key=entity_key,
                rpc_url=arkiv_rpc,
                private_key=private_key,
            )
        else:
            console.print("[yellow]Warning: HAVEN_PRIVATE_KEY not set, cannot fetch from Arkiv[/yellow]")
    
    if not metadata:
        raise ValueError(
            f"No encryption metadata found for CID {cid}. "
            "Ensure the file was encrypted during upload, provide a sidecar .encmeta file, "
            "or pass --entity-key to fetch metadata from Arkiv."
        )
    
    icp_identity_path = os.environ.get("HAVEN_ICP_IDENTITY_PEM_PATH", "").strip()
    if not icp_identity_path:
        raise ValueError("HAVEN_ICP_IDENTITY_PEM_PATH is required for Haven-AOL ICP decryption")

    gate_source = metadata.access_control_conditions[0] if metadata.access_control_conditions else {}
    token_address = str(gate_source.get("contractAddress", "")).strip()
    threshold = derivation_threshold_from_access_condition(gate_source)
    cid_value = str(gate_source.get("cid") or cid or "").strip()
    if not cid_value:
        cid_value = f"local-{input_path.name}"

    if not token_address:
        raise ValueError("Encryption metadata missing token contract address")

    if not metadata.chain:
        raise ValueError(
            "Encryption metadata is missing chain. "
            "Cannot decrypt without the access-control asset chain (EVM)."
        )

    gate = GateParams(
        chain=normalize_haven_aol_chain(str(metadata.chain)),
        token_address=token_address,
        threshold=threshold,
        cid=cid_value,
    )

    if input_path.resolve() == output_path.resolve():
        with tempfile.NamedTemporaryFile(
            suffix=output_path.suffix or ".tmp",
            dir=str(output_path.parent),
            delete=False,
        ) as tmp_file:
            temp_output_path = Path(tmp_file.name)
        try:
            decrypt_file_streaming(
                input_path=input_path,
                output_path=temp_output_path,
                private_key="",
                encrypted_key_b64=metadata.encrypted_key,
                gate=gate,
            )
            temp_output_path.replace(output_path)
        finally:
            if temp_output_path.exists():
                temp_output_path.unlink()
    else:
        decrypt_file_streaming(
            input_path=input_path,
            output_path=output_path,
            private_key="",
            encrypted_key_b64=metadata.encrypted_key,
            gate=gate,
        )


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Human-readable size string
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


@app.command()
def info(
    cid: str = typer.Argument(
        ...,
        help="Content ID (CID) to get information about.",
    ),
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output in JSON format.",
    ),
) -> None:
    """Get information about a file stored on Filecoin.
    
    Retrieves storage deal information, status, and metadata for a CID.
    
    Example:
        haven download info bafybeig...
        haven download info bafybeig... --json
    """
    config = load_config(config_file)
    
    # Strip whitespace from CID (handle potential newline issues)
    cid = cid.strip()
    
    # Validate CID format
    if not verify_cid_format(cid):
        console.print(f"[red]✗[/red] Invalid CID format: {cid}")
        console.print("[yellow]CIDs should start with 'Qm' (CIDv0) or 'baf' (CIDv1)[/yellow]")
        raise typer.Exit(code=1)
    
    async def get_info() -> None:
        try:
            manager = JSBridgeManager.get_instance()
            
            async with manager:
                # Connect to Synapse (uses FILECOIN_RPC_URL and HAVEN_PRIVATE_KEY env vars)
                await js_call(
                    JSRuntimeMethods.SYNAPSE_CONNECT,
                    {},
                    max_retries=3,
                )
                
                # Get status from Synapse
                status = await js_call(
                    JSRuntimeMethods.SYNAPSE_GET_STATUS,
                    {"cid": cid},
                    max_retries=3,
                )
                
                if json_output:
                    # Output as JSON
                    console.print(json.dumps(status, indent=2))
                else:
                    # Output as formatted text
                    _print_status_table(cid, status)
                    
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to get info: {e}")
            raise typer.Exit(code=1)
    
    asyncio.run(get_info())


def _print_status_table(cid: str, status: dict) -> None:
    """Print status information in a formatted table.
    
    Args:
        cid: Content ID
        status: Status dictionary from Synapse
    """
    console.print()
    console.print(f"[bold]CID:[/bold] {cid}")
    
    # Main status
    status_text = status.get("status", "unknown")
    if status_text.lower() in ("active", "completed", "success"):
        status_color = "green"
    elif status_text.lower() in ("pending", "processing"):
        status_color = "yellow"
    else:
        status_color = "red"
    
    console.print(f"[bold]Status:[/bold] [{status_color}]{status_text}[/{status_color}]")
    
    # Additional metadata
    if "size" in status:
        console.print(f"[bold]Size:[/bold] {_format_file_size(status['size'])}")
    
    if "mimeType" in status:
        console.print(f"[bold]MIME Type:[/bold] {status['mimeType']}")
    
    if "createdAt" in status:
        console.print(f"[bold]Created:[/bold] {status['createdAt']}")
    
    # Storage deals
    deals = status.get("deals", [])
    if deals:
        console.print()
        table = Table(title="Storage Deals")
        table.add_column("Deal ID", style="cyan")
        table.add_column("Provider", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("Start Epoch", justify="right")
        table.add_column("End Epoch", justify="right")
        
        for deal in deals:
            deal_status = deal.get("status", "unknown")
            if deal_status.lower() in ("active", "slashed"):
                status_style = "green"
            elif deal_status.lower() in ("pending", "published"):
                status_style = "yellow"
            else:
                status_style = "red"
            
            table.add_row(
                str(deal.get("dealId", "N/A")),
                deal.get("provider", "N/A"),
                f"[{status_style}]{deal_status}[/{status_style}]",
                str(deal.get("startEpoch", "N/A")),
                str(deal.get("endEpoch", "N/A")),
            )
        
        console.print(table)
    else:
        console.print("[dim]No storage deals found[/dim]")
    
    # Replicas info
    if "replicas" in status:
        console.print(f"\n[bold]Replicas:[/bold] {status['replicas']}")
    
    # Check database for local metadata
    try:
        from haven_cli.database.connection import get_db_session
        from haven_cli.database.repositories import VideoRepository
        
        with get_db_session() as session:
            video_repo = VideoRepository(session)
            video = video_repo.get_by_cid(cid)
            
            if video:
                console.print()
                console.print("[bold]Local Metadata:[/bold]")
                console.print(f"  Title: {video.title}")
                console.print(f"  Duration: {video.duration:.2f}s" if video.duration else "  Duration: N/A")
                console.print(f"  Encrypted: {'Yes' if video.encrypted else 'No'}")
                
                if video.arkiv_entity_key:
                    console.print(f"  Arkiv Entity Key: {video.arkiv_entity_key}")
    except Exception:
        # Don't fail if database lookup fails
        pass


@app.command()
def decrypt_file(
    input_path: Path = typer.Argument(
        ...,
        help="Path to the encrypted file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path for decrypted file (default: input file without .enc extension).",
        file_okay=True,
        dir_okay=False,
    ),
    cid: Optional[str] = typer.Option(
        None,
        "--cid",
        help="CID for metadata lookup (if different from file).",
    ),
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
    ),
    entity_key: Optional[str] = typer.Option(
        None,
        "--entity-key",
        help="Arkiv entity key for encryption metadata lookup (if not in local DB).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing file if it exists.",
    ),
) -> None:
    """Decrypt a local file using Haven-AOL.
    
    This command decrypts a file that was encrypted with Haven-AOL.
    Encryption metadata is looked up from the database (by CID), from
    a sidecar .encmeta file, or from an Arkiv entity (--entity-key).
    
    Example:
        haven download decrypt-file encrypted.mp4 --output decrypted.mp4
        haven download decrypt-file encrypted.mp4 --cid bafybeig...
        haven download decrypt-file encrypted.mp4 --entity-key 0x1234...
    """
    config = load_config(config_file)
    
    # Determine output path
    if output is None:
        # Remove .enc extension if present
        if input_path.suffix == ".enc":
            output = input_path.with_suffix("")
        else:
            output = input_path.parent / f"{input_path.stem}_decrypted{input_path.suffix}"
    
    # Check if output already exists
    if output.exists() and not force:
        console.print(f"[red]✗[/red] Output file already exists: {output}")
        console.print("[yellow]Use --force to overwrite[/yellow]")
        raise typer.Exit(code=1)
    
    console.print(f"[bold]Decrypting:[/bold] {input_path}")
    console.print(f"[bold]Output:[/bold] {output}")
    
    if cid:
        console.print(f"[bold]CID:[/bold] {cid}")
    
    async def run_decrypt() -> None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Decrypting...")
            
            try:
                await _decrypt_file(
                    input_path,
                    output,
                    cid or "",
                    entity_key=entity_key,
                )
                
                progress.update(task, completed=True)
                
                console.print(f"[green]✓[/green] Decryption complete: {output}")
                
                # Show file info
                file_size = output.stat().st_size if output.exists() else 0
                if file_size > 0:
                    size_str = _format_file_size(file_size)
                    console.print(f"[dim]File size: {size_str}[/dim]")
                
            except ValueError as e:
                console.print(f"[red]✗[/red] {e}")
                raise typer.Exit(code=1)
            except Exception as e:
                console.print(f"[red]✗[/red] Decryption failed: {e}")
                
                # Clean up partial output
                if output.exists():
                    try:
                        output.unlink()
                    except OSError:
                        pass
                
                raise typer.Exit(code=1)
    
    asyncio.run(run_decrypt())
