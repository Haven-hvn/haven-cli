"""``haven dataset`` — dataset-per-epoch lifecycle (plan item 12).

- ``haven dataset list`` shows live Filecoin datasets (from
  ``synapse.listDataSets``) merged with local piece counts, epoch
  labels (from gate metadata), and the recorded epoch map.
- ``haven dataset terminate <id>`` is the explicit operator action
  that ends an epoch dataset's seed.  It is gated on the accounting
  check: every piece in the dataset must also exist in another
  dataset (no sole-copy pieces), and the operator must pass
  ``--yes``.  Termination is never automatic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    help="Inspect and terminate Filecoin datasets (per-epoch lifecycle).",
    no_args_is_help=True,
)
console = Console()


async def _js_call(method: str, params: dict, config: object = None) -> dict:
    """Call a synapse method (connecting first, like the upload path)."""
    from haven_cli.js_runtime.manager import JSBridgeManager
    from haven_cli.services.blockchain_network import get_network_config

    manager = JSBridgeManager.get_instance()
    filecoin_mode = getattr(
        getattr(config, "blockchain", None),
        "effective_filecoin_network_mode",
        "testnet",
    )
    try:
        rpc_url = get_network_config(filecoin_mode).filecoin_rpc_url
    except Exception:
        rpc_url = None
    await manager.call_with_retry(
        "synapse.connect",
        {"rpcUrl": rpc_url, "networkMode": filecoin_mode},
        timeout=120.0,
        max_retries=1,
    )
    result: dict = await manager.call_with_retry(method, params, max_retries=1)
    return result


def _load_config_or_die(config_file: Path | None) -> object:
    from haven_cli.config import load_config

    return load_config(config_file)


@app.command(name="list")
def list_datasets(
    config_file: Path | None = typer.Option(
        None, "--config", "-c", help="Path to configuration file.",
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output in JSON format.",
    ),
) -> None:
    """List datasets with per-epoch lockup/piece totals."""
    import asyncio
    import json

    from haven_cli.services.datasets import list_local_datasets, load_epoch_map

    config = _load_config_or_die(config_file)
    data_dir = getattr(config, "data_dir", None)

    async def _chain_rows() -> list:
        try:
            result: dict = await _js_call("synapse.listDataSets", {}, config)
            if isinstance(result, list):
                rows: list = result
                return rows
            if isinstance(result, dict) and isinstance(result.get("dataSets"), list):
                nested: list = result["dataSets"]
                return nested
            return []
        except Exception as exc:
            console.print(f"[yellow]![/yellow] could not list chain datasets: {exc}")
            return []

    chain_rows = asyncio.run(_chain_rows())
    chain_by_id = {str(row.get("dataSetId")): row for row in chain_rows if isinstance(row, dict)}

    try:
        local_views = list_local_datasets()
    except Exception as exc:
        console.print(f"[red]✗[/red] could not read local datasets: {exc}")
        sys.exit(1)
    epoch_map = load_epoch_map(data_dir) if data_dir is not None else {}
    local_by_id = {view.dataset_id: view for view in local_views}

    dataset_ids = sorted(set(chain_by_id) | set(local_by_id) | set(epoch_map.values()))

    if json_output:
        rows = []
        for ds in dataset_ids:
            chain = chain_by_id.get(ds, {})
            local = local_by_id.get(ds)
            rows.append({
                "dataset_id": ds,
                "provider_id": str(chain.get("providerId")) if chain.get("providerId") is not None else None,
                "is_live": chain.get("isLive") if "isLive" in chain else None,
                "piece_count": local.piece_count if local else 0,
                "epochs": local.epochs if local else [],
                "last_upload": local.last_upload.isoformat() if local and local.last_upload else None,
            })
        console.print(json.dumps(rows, indent=2, default=str))
        return

    table = Table(title="Filecoin datasets (chain + local)")
    table.add_column("dataset")
    table.add_column("provider")
    table.add_column("live")
    table.add_column("pieces")
    table.add_column("epochs")
    table.add_column("last upload")
    for ds in dataset_ids:
        chain = chain_by_id.get(ds, {})
        local = local_by_id.get(ds)
        table.add_row(
            ds,
            str(chain.get("providerId")) if chain.get("providerId") is not None else "—",
            str(chain.get("isLive")) if "isLive" in chain else "—",
            str(local.piece_count) if local else "0",
            ", ".join(str(e) for e in (local.epochs if local else [])) or "—",
            local.last_upload.isoformat() if local and local.last_upload else "—",
        )
    console.print(table)
    if epoch_map:
        for epoch, ds in sorted(epoch_map.items()):
            console.print(f"epoch {epoch} → dataset {ds}")


@app.command(name="terminate")
def terminate_dataset(
    dataset_id: str = typer.Argument(..., help="Dataset ID to terminate."),
    config_file: Path | None = typer.Option(
        None, "--config", "-c", help="Path to configuration file.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm termination after the accounting report is reviewed.",
    ),
) -> None:
    """Terminate a dataset (explicit operator action, accounting-gated)."""
    import asyncio

    from haven_cli.services.datasets import account_dataset

    config = _load_config_or_die(config_file)

    try:
        accounting = account_dataset(dataset_id)
    except Exception as exc:
        console.print(f"[red]✗[/red] accounting failed: {exc}")
        sys.exit(1)

    console.print(f"Dataset {accounting.dataset_id}: {len(accounting.pieces)} piece(s)")
    if accounting.epochs:
        console.print(f"Epochs present: {', '.join(str(e) for e in accounting.epochs)}")
    if accounting.sole_copy_pieces:
        console.print(
            f"[red]✗[/red] {len(accounting.sole_copy_pieces)} sole-copy piece(s) "
            "exist only in this dataset:"
        )
        for piece in accounting.sole_copy_pieces:
            console.print(f"  {piece}")
        console.print(
            "Re-upload each into another dataset first (or use `haven repin`), "
            "then retry. Refusing to terminate."
        )
        sys.exit(1)

    console.print("No sole-copy pieces — every piece also lives elsewhere.")
    if not yes:
        console.print("Re-run with --yes to terminate (this ends the seed for this dataset).")
        return

    async def _terminate() -> dict:
        result: dict = await _js_call(
            "synapse.terminateDataSet", {"dataSetId": str(dataset_id)}, config
        )
        return result

    try:
        result = asyncio.run(_terminate())
    except Exception as exc:
        console.print(f"[red]✗[/red] termination failed: {exc}")
        sys.exit(1)

    console.print(f"✓ Dataset {dataset_id} terminated: {result}")
