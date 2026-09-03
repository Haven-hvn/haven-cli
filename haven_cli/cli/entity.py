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
    arkiv_config = build_arkiv_config(
        private_key=config.blockchain.private_key if config else None,
        rpc_url=config.blockchain.arkiv_rpc_url if config else None,
        enabled=True,  # Force enable for query
    )
    
    async def fetch_entity() -> None:
        try:
            # Import arkiv SDK
            from arkiv import Arkiv
            from arkiv.account import NamedAccount
            from arkiv.provider import ProviderBuilder
            from arkiv.types import KEY, ATTRIBUTES, PAYLOAD, CONTENT_TYPE, OWNER, CREATED_AT, UPDATED_AT
            
            # Create provider and account
            provider = ProviderBuilder().custom(arkiv_config.rpc_url).build()
            account = NamedAccount.from_private_key("haven-cli", arkiv_config.private_key)
            client = Arkiv(provider=provider, account=account)
            
            # Query entity
            from arkiv.types import EntityKey
            key = EntityKey(entity_key)
            
            entity = client.arkiv.get_entity(
                key,
                attributes=KEY | ATTRIBUTES | PAYLOAD | CONTENT_TYPE | OWNER | CREATED_AT | UPDATED_AT
            )
            
            if not entity:
                console.print(f"[red]✗[/red] Entity not found: {entity_key}")
                raise typer.Exit(code=1)
            
            # Parse payload
            payload = {}
            if hasattr(entity, 'payload') and entity.payload:
                try:
                    if raw:
                        payload = {"raw": entity.payload.decode('utf-8', errors='replace')}
                    else:
                        payload = json.loads(entity.payload.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    payload = {"error": f"Failed to parse payload: {e}", "raw": str(entity.payload)[:200]}
            
            # Get attributes
            attributes = {}
            if hasattr(entity, 'attributes') and entity.attributes:
                attributes = dict(entity.attributes)
            
            # Build result
            result = {
                "entity_key": str(entity.key) if hasattr(entity, 'key') else entity_key,
                "owner": str(entity.owner) if hasattr(entity, 'owner') else None,
                "content_type": str(entity.content_type) if hasattr(entity, 'content_type') else None,
                "created_at": str(entity.created_at) if hasattr(entity, 'created_at') else None,
                "updated_at": str(entity.updated_at) if hasattr(entity, 'updated_at') else None,
                "payload": payload,
                "attributes": attributes,
            }
            
            if json_output:
                console.print(json.dumps(result, indent=2, default=str))
            else:
                _print_entity_table(result)
                
        except ImportError:
            console.print("[red]✗[/red] arkiv package is required for entity queries.")
            console.print("Install with: pip install arkiv")
            raise typer.Exit(code=1)
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
    arkiv_config = build_arkiv_config(
        private_key=config.blockchain.private_key if config else None,
        rpc_url=config.blockchain.arkiv_rpc_url if config else None,
        enabled=True,
    )
    
    async def do_query() -> None:
        try:
            from arkiv import Arkiv
            from arkiv.account import NamedAccount
            from arkiv.provider import ProviderBuilder
            from arkiv.types import KEY, ATTRIBUTES, PAYLOAD, CONTENT_TYPE, OWNER, CREATED_AT, QueryOptions
            
            # Create provider and account
            provider = ProviderBuilder().custom(arkiv_config.rpc_url).build()
            account = NamedAccount.from_private_key("haven-cli", arkiv_config.private_key)
            client = Arkiv(provider=provider, account=account)
            
            # Build query options
            query_options = QueryOptions(
                attributes=KEY | ATTRIBUTES | CONTENT_TYPE | OWNER | CREATED_AT,
                max_results_per_page=limit,
            )
            
            # Execute query
            entities = list(client.arkiv.query_entities(
                query=query,
                options=query_options
            ))
            
            if not entities:
                console.print(f"[yellow]No entities found for query:[/yellow] {query}")
                return
            
            # Build results
            results = []
            for entity in entities[:limit]:
                result = {
                    "entity_key": str(entity.key) if hasattr(entity, 'key') else None,
                    "owner": str(entity.owner) if hasattr(entity, 'owner') else None,
                    "content_type": str(entity.content_type) if hasattr(entity, 'content_type') else None,
                    "created_at": str(entity.created_at) if hasattr(entity, 'created_at') else None,
                    "attributes": dict(entity.attributes) if hasattr(entity, 'attributes') else {},
                }
                results.append(result)
            
            if json_output:
                console.print(json.dumps(results, indent=2, default=str))
            else:
                _print_query_results(results, query)
                
        except ImportError:
            console.print("[red]✗[/red] arkiv package is required for entity queries.")
            console.print("Install with: pip install arkiv")
            raise typer.Exit(code=1)
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
