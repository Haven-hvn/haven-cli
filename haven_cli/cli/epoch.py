"""``haven epoch`` subcommand — print the local and canister v3 epochs.

Per ``tasking/README.md`` Key Design Decision §4 and the Sprint 4 brief,
this is the ONLY haven-cli code path allowed to call the canister's
``getCurrentEpoch`` query. Upload and decrypt paths must derive epochs
locally from :func:`haven_aol.v3.current_epoch`. The two numbers are
expected to match within ±1 epoch (≈30 days) under normal operation —
significant drift is an operator signal that the host wall clock is
wrong, not that the canister has changed protocol.
"""

from __future__ import annotations

import logging
import sys

import typer
from rich.console import Console

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Print the local and canister v3 epoch values.",
    no_args_is_help=False,
)
console = Console()


@app.command(name="show")
def show(
    *,
    no_canister: bool = typer.Option(
        False,
        "--no-canister",
        help=(
            "Skip the canister query and print only the local epoch. "
            "Useful for offline diagnostics or when the canister is "
            "unreachable."
        ),
    ),
) -> None:
    """Print ``local epoch`` and (unless ``--no-canister``) ``canister epoch``.

    Example output (shape, not exact values)::

        local epoch:    687
        canister epoch: 687

    Drift handling:
      * If the two values are equal, the wallet's local clock agrees with
        the canister. Normal.
      * If they differ by 1, the wallet is on a different side of an
        epoch boundary than the canister — also normal at boundary
        transitions (epoch length is 30 days; this happens once a month).
      * If they differ by ≥2, the local wall clock is significantly skewed.
        Decryption of fresh content may fail under v3. Fix the host clock.
    """
    # Import the SDK lazily so this command stays importable on systems
    # that haven't yet built ``haven_aol`` (e.g. CI before the
    # package-install step).
    from haven_aol.v3 import current_epoch

    local = current_epoch()
    console.print(f"local epoch:    {local}")

    if no_canister:
        logger.info("haven epoch: local=%d (canister query skipped)", local)
        return

    try:
        from haven_cli.services.haven_aol_icp import get_current_epoch
    except ImportError as exc:
        console.print(
            "[red]✗[/red] haven-cli ICP support is not installed. "
            f"Install haven-cli[icp] and retry. ({exc})"
        )
        sys.exit(1)

    try:
        canister = get_current_epoch()
    except Exception as exc:
        console.print(f"[yellow]![/yellow] canister query failed: {exc}")
        logger.error("haven epoch: canister query failed: %s", exc)
        sys.exit(1)

    console.print(f"canister epoch: {canister}")
    drift = abs(canister - local)
    if drift == 0:
        logger.info("haven epoch: local=%d canister=%d (in sync)", local, canister)
    elif drift == 1:
        logger.info(
            "haven epoch: local=%d canister=%d (drift=1, expected at epoch boundary)",
            local, canister,
        )
        console.print(
            "[yellow]note:[/yellow] drift of 1 epoch is normal near an epoch boundary."
        )
    else:
        logger.warning(
            "haven epoch: local=%d canister=%d drift=%d epochs",
            local, canister, drift,
        )
        console.print(
            f"[red]warning:[/red] drift of {drift} epochs — check the host wall clock."
        )
