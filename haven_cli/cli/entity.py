"""Haven entity command - Query and manage Arkiv entities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table

from haven_cli.config import load_config

app = typer.Typer(
    help="Query and manage Arkiv blockchain entities.",
    no_args_is_help=True,
)
console = Console()


@app.command(name="get")
def get_entity(
    entity_key: str = typer.Argument(
        ...,
        help="Arkiv entity key to query.",
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
        "-j",
        help="Output in JSON format.",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        "-r",
        help="Output raw payload bytes without parsing.",
    ),
) -> None:
    """Get an Arkiv entity by its key.
    
    Retrieves the entity from the Arkiv blockchain and displays its
    payload, attributes, and metadata. Useful for verifying uploads
    and debugging.
    
    Example:
        haven entity get 0x1234...abcd
        haven entity get 0x1234...abcd --json
        haven entity get 0x1234...abcd --raw
    """
    import asyncio
    
    from haven_cli.services.arkiv_sync import build_arkiv_config
    
    config = load_config(config_file)
    # B3: ``BlockchainConfig`` has no ``private_key`` / ``arkiv_rpc_url``
    # attributes — reading them raised AttributeError. Auth comes solely
    # from HAVEN_PRIVATE_KEY (build_arkiv_config falls back to it) and the
    # RPC URL is derived from network mode / env / the optional override.
    arkiv_config = build_arkiv_config(
        rpc_url=config.blockchain.arkiv_rpc_override if config else None,
        enabled=True,  # Force enable for query
    )
    
    async def fetch_entity() -> None:
        try:
            from haven_cli.crypto.metadata import (
                arkiv_get_entity,
                arkiv_row_attributes,
            )

            row = arkiv_get_entity(arkiv_config.rpc_url, entity_key)
            if not row:
                console.print(f"[red]✗[/red] Entity not found: {entity_key}")
                raise typer.Exit(code=1)

            attributes = arkiv_row_attributes(row)

            # Parse payload (stored as a plain `payload` bytes attribute)
            payload = {}
            payload_bytes = attributes.get("payload")
            if isinstance(payload_bytes, bytes):
                text = payload_bytes.decode("utf-8", errors="replace")
                if raw:
                    payload = {"raw": text}
                else:
                    try:
                        payload = json.loads(text)
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        payload = {
                            "error": f"Failed to parse payload: {e}",
                            "raw": text[:200],
                        }

            # Build result
            result = {
                "entity_key": row.get("key") or entity_key,
                "owner": row.get("owner"),
                "content_type": attributes.get("content_type"),
                "created_at": row.get("createdAt"),
                "updated_at": row.get("updatedAt"),
                "payload": payload,
                "attributes": {k: v for k, v in attributes.items() if k != "payload"},
            }

            if json_output:
                console.print(json.dumps(result, indent=2, default=str))
            else:
                _print_entity_table(result)

        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to fetch entity: {e}")
            raise typer.Exit(code=1)
    
    asyncio.run(fetch_entity())


@app.command(name="query")
def query_entities(
    query: str = typer.Argument(
        ...,
        help="Arkiv query string (e.g., 'sha256_ct = \"abc123\"').",
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
        "-j",
        help="Output in JSON format.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of results to return.",
    ),
) -> None:
    """Query Arkiv entities using attribute filters.

    Search for entities using Arkiv's query syntax. Common queries:
    - By locator hash: 'sha256_ct = "abc123..."'
    - By title: 'title = "My Video"'
    - By group: 'grp = "haven.video.full"'
    - By gate class: 'gate_type = 1'

    Example:
        haven entity query 'sha256_ct = "abc123..."'
        haven entity query 'title = "Test Video"' --limit 5
        haven entity query 'grp = "haven.video.full"' --json
    """
    import asyncio
    
    from haven_cli.services.arkiv_sync import build_arkiv_config
    
    config = load_config(config_file)
    # B3: see get_entity above — no private_key/arkiv_rpc_url attributes.
    arkiv_config = build_arkiv_config(
        rpc_url=config.blockchain.arkiv_rpc_override if config else None,
        enabled=True,
    )
    
    async def do_query() -> None:
        try:
            from haven_cli.crypto.metadata import (
                arkiv_query,
                arkiv_row_attributes,
                arkiv_row_payload,
            )

            rows = arkiv_query(
                arkiv_config.rpc_url,
                query,
                limit=limit,
                select={
                    "key": True,
                    "owner": True,
                    "creator": True,
                    "createdAt": True,
                    "updatedAt": True,
                    "expiresAt": True,
                    "attributes": True,
                },
            )

            if not rows:
                console.print(f"[yellow]No entities found for query:[/yellow] {query}")
                return

            # Build results
            results = []
            for row in rows[:limit]:
                attrs = arkiv_row_attributes(row)
                results.append({
                    "entity_key": row.get("key"),
                    "owner": row.get("owner"),
                    "content_type": attrs.get("content_type"),
                    "created_at": row.get("createdAt"),
                    "attributes": {k: v for k, v in attrs.items() if k != "payload"},
                    "payload": arkiv_row_payload(row),
                })

            if json_output:
                console.print(json.dumps(results, indent=2, default=str))
            else:
                _print_query_results(results, query)

        except Exception as e:
            console.print(f"[red]✗[/red] Query failed: {e}")
            raise typer.Exit(code=1)
    
    asyncio.run(do_query())


def _print_entity_table(result: dict) -> None:
    """Print entity information in a formatted table."""
    console.print()
    console.print(Panel(f"[bold]Entity:[/bold] {result['entity_key']}", expand=False))
    
    # Metadata table
    meta_table = Table(title="Entity Metadata", show_header=True)
    meta_table.add_column("Field", style="cyan")
    meta_table.add_column("Value", style="white")
    
    meta_table.add_row("Owner", result.get('owner', 'N/A') or 'N/A')
    meta_table.add_row("Content Type", result.get('content_type', 'N/A') or 'N/A')
    meta_table.add_row("Created", result.get('created_at', 'N/A') or 'N/A')
    if result.get('updated_at'):
        meta_table.add_row("Updated", result['updated_at'])
    
    console.print(meta_table)
    
    # Attributes table
    if result.get('attributes'):
        attr_table = Table(title="Public Attributes (Indexed)", show_header=True)
        attr_table.add_column("Attribute", style="cyan")
        attr_table.add_column("Value", style="green")
        
        for key, value in sorted(result['attributes'].items()):
            # Truncate long values
            value_str = str(value)
            if len(value_str) > 80:
                value_str = value_str[:77] + "..."
            attr_table.add_row(key, value_str)
        
        console.print(attr_table)
    
    # Payload
    if result.get('payload'):
        console.print(Panel("[bold]Private Payload[/bold]", expand=False))
        
        if isinstance(result['payload'], dict):
            payload_table = Table(show_header=True)
            payload_table.add_column("Field", style="cyan")
            payload_table.add_column("Value", style="yellow")
            
            for key, value in sorted(result['payload'].items()):
                # Format value based on type
                if isinstance(value, dict):
                    value_str = json.dumps(value, indent=2)
                elif isinstance(value, list):
                    value_str = json.dumps(value)
                else:
                    value_str = str(value)
                
                # Truncate long values
                if len(value_str) > 80:
                    value_str = value_str[:77] + "..."
                
                payload_table.add_row(key, value_str)
            
            console.print(payload_table)
        else:
            console.print(str(result['payload']))


def _print_query_results(results: list, query: str) -> None:
    """Print query results in a formatted table."""
    console.print()
    console.print(Panel(f"[bold]Query:[/bold] {query}\n[bold]Results:[/bold] {len(results)}", expand=False))
    
    table = Table(show_header=True)
    table.add_column("Entity Key", style="cyan", no_wrap=True, width=30)
    table.add_column("Title", style="green")
    table.add_column("Creator", style="magenta")
    table.add_column("Encrypted", style="yellow", justify="center")
    table.add_column("Created", style="white")
    
    for result in results:
        attrs = result.get('attributes', {})
        table.add_row(
            result.get('entity_key', 'N/A')[:28] + "..." if result.get('entity_key') else 'N/A',
            attrs.get('title', 'N/A')[:30] or 'N/A',
            attrs.get('creator_handle', '-') or '-',
            "✓" if attrs.get('is_encrypted') else "",
            result.get('created_at', 'N/A')[:19] if result.get('created_at') else 'N/A',
        )
    
    console.print(table)
    console.print(f"\n[dim]Use 'haven entity get <entity_key>' for full details[/dim]")
