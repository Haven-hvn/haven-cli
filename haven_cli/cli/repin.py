"""``haven repin`` — popularity-driven retention (plan item 11).

Given an Arkiv entity ID or a Filecoin piece CID, fetch the
ciphertext, upload it to the caller's own dataset (``copies=1``,
dataset reuse), and publish an Arkiv entity pointing at the new copy
with the original ``gate_epoch`` preserved.  No re-encryption, no
original torrent required.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console

from haven_cli.config import load_config

console = Console()


def repin_command(
    target: str = typer.Argument(
        ...,
        help="Arkiv entity key (0x…) or Filecoin piece CID (bafk…).",
    ),
    config_file: Path | None = typer.Option(
        None, "--config", "-c", help="Path to configuration file.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Directory for the fetched ciphertext (default: downloads/repin).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve the target and print the repin plan; no network writes.",
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Print the result as JSON.",
    ),
) -> None:
    """Repin a seeded video into your own dataset.

    Example:
        haven repin 0x<entity-key>
        haven repin bafk<full-piece-cid> --dry-run
    """
    import json

    from haven_cli.services.repin import repin as _repin

    # Loading config sets the HAVEN_SYNAPSE_* env the JS wrapper reads
    # (copies=1 + provider pins), so the repin upload matches seeding.
    load_config(config_file)

    async def _run() -> None:
        try:
            result = await _repin(
                target,
                output_dir=output_dir,
                dry_run=dry_run,
            )
        except Exception as exc:
            console.print(f"[red]✗[/red] Repin failed: {exc}")
            sys.exit(1)

        payload = {
            "target": target,
            "piece_cid": result.target.piece_cid,
            "dataset_id": result.target.dataset_id,
            "gate_epoch": (result.target.gate or {}).get("epoch"),
            "downloaded_path": result.downloaded_path,
            "new_piece_cid": result.piece_cid,
            "new_root_cid": result.root_cid,
            "dataset_id_new": result.dataset_id,
            "entity_key": result.entity_key,
            "transaction_hash": result.transaction_hash,
            "retrievable": result.retrievable,
            "dry_run": result.dry_run,
            "error": result.error,
        }
        if json_output:
            console.print(json.dumps(payload, indent=2, default=str))
        elif result.error:
            console.print(f"[red]✗[/red] Repin failed: {result.error}")
            sys.exit(1)
        elif dry_run:
            table = [
                f"piece:   {payload['piece_cid']}",
                f"dataset: {payload['dataset_id']}",
                f"gate:    epoch {payload['gate_epoch']} (preserved verbatim)",
                "plan:    download ciphertext -> upload copies=1 (reused "
                "dataset) -> verify retrievability -> create new Arkiv "
                "entity pointing at the new copy",
            ]
            for line in table:
                console.print(line)
        else:
            console.print(f"✓ Repin complete: new piece {result.piece_cid}")
            console.print(f"✓ Arkiv entity: {result.entity_key}")
            console.print(f"✓ Transaction: {result.transaction_hash}")

    asyncio.run(_run())
