"""
JS Runtime Discovery.

Haven's JS services (``js-services/main.ts`` and everything it imports) are
Deno programs: they use ``npm:`` specifiers (``npm:@filoz/synapse-sdk``,
``npm:ethers``, ...) and Deno globals (``Deno.stdin``, ``Deno.exit``,
``Deno.hostname``). Neither Node.js (even with tsx/ts-node) nor Bun can run
them — Node rejects the ``npm:`` scheme with
``ERR_UNSUPPORTED_ESM_URL_SCHEME``.

The four crashed daemon launches on 2026-11-10 were caused by this module
falling back to Node + tsx/ts-node when Deno was not selected. To make that
failure mode impossible, discovery now resolves **only** Deno and fails
loudly if it is unavailable. See docs/PRIORITIZED_PLAN.md item 1 (B7).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class RuntimeType(Enum):
    """Supported JavaScript runtime types.

    Only ``DENO`` is supported for the shipped JS services. ``NODE`` / ``BUN``
    are retained as enum members so external code that references them keeps
    working, but they are never discovered or launched.
    """

    DENO = auto()
    NODE = auto()
    BUN = auto()


@dataclass
class RuntimeInfo:
    """Information about a discovered runtime."""

    type: RuntimeType
    executable: str
    version: Optional[str] = None
    path: Optional[Path] = None

    @property
    def display_name(self) -> str:
        """Get a display name for the runtime."""
        version_str = f" v{self.version}" if self.version else ""
        return f"{self.type.name.title()}{version_str}"


# Runtime detection order (preferred first). Only the shipped Deno runtime
# is supported: main.ts uses npm: specifiers + Deno globals (B7).
RUNTIME_PREFERENCE = [
    RuntimeType.DENO,
]

# Executable names for each runtime
RUNTIME_EXECUTABLES = {
    RuntimeType.DENO: ["deno"],
    RuntimeType.NODE: ["node", "nodejs"],
    RuntimeType.BUN: ["bun"],
}

# Version check commands
VERSION_COMMANDS = {
    RuntimeType.DENO: ["--version"],
    RuntimeType.NODE: ["--version"],
    RuntimeType.BUN: ["--version"],
}


async def discover_runtime(
    preferred: Optional[RuntimeType] = None
) -> str:
    """
    Discover the shipped JavaScript runtime (Deno only).

    Args:
        preferred: Ignored unless it is ``DENO``; retained for API
            compatibility. Node/Bun are never returned.

    Returns:
        Path to the Deno executable.

    Raises:
        RuntimeError: If Deno is not found on PATH.
    """
    # Search order is Deno-only. A non-DENO preference cannot be honoured
    # because the JS services cannot run under any other runtime.
    search_order = list(RUNTIME_PREFERENCE)
    if preferred == RuntimeType.DENO and RuntimeType.DENO in search_order:
        search_order.remove(RuntimeType.DENO)
        search_order.insert(0, RuntimeType.DENO)

    for runtime_type in search_order:
        info = await _detect_runtime(runtime_type)
        if info:
            logger.info(f"Discovered runtime: {info.display_name}")
            return info.executable

    raise RuntimeError(
        "Deno is required to run the Haven JS services. No Deno executable "
        "was found on PATH.\n"
        "The JS services use 'npm:' specifiers and Deno globals; Node.js "
        "and Bun cannot run them (tsx/ts-node produce "
        "ERR_UNSUPPORTED_ESM_URL_SCHEME).\n"
        "Install Deno from https://deno.land and ensure 'deno' is on PATH."
    )


async def discover_all_runtimes() -> list[RuntimeInfo]:
    """
    Discover all available JavaScript runtimes.

    Only the Deno runtime is considered supported; the returned list
    therefore contains at most one entry.
    """
    runtimes = []

    for runtime_type in RUNTIME_PREFERENCE:
        info = await _detect_runtime(runtime_type)
        if info:
            runtimes.append(info)

    return runtimes


async def _detect_runtime(runtime_type: RuntimeType) -> Optional[RuntimeInfo]:
    """Detect a specific runtime type."""
    executables = RUNTIME_EXECUTABLES.get(runtime_type, [])

    for executable in executables:
        path = shutil.which(executable)
        if path:
            version = await _get_version(path, runtime_type)
            return RuntimeInfo(
                type=runtime_type,
                executable=path,
                version=version,
                path=Path(path)
            )

    return None


async def _get_version(executable: str, runtime_type: RuntimeType) -> Optional[str]:
    """Get the version of a runtime."""
    try:
        args = VERSION_COMMANDS.get(runtime_type, ["--version"])
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)

        output = stdout.decode().strip()

        # Parse version from output
        if runtime_type == RuntimeType.DENO:
            # Output: "deno 1.x.x"
            for line in output.split("\n"):
                if line.startswith("deno"):
                    return line.split()[1]
        elif runtime_type == RuntimeType.NODE:
            # Output: "v18.x.x"
            return output.lstrip("v").split()[0]
        elif runtime_type == RuntimeType.BUN:
            # Output: "1.x.x"
            return output.split()[0]

        return output.split()[0] if output else None

    except Exception as e:
        logger.debug(f"Failed to get version for {executable}: {e}")
        return None


def get_runtime_args(
    executable: str,
    entry_point: Path,
    debug: bool = False
) -> list[str]:
    """
    Get command-line arguments for running the JS services.

    The shipped services are Deno-only. ``get_runtime_args`` refuses to build
    a command for a non-Deno executable instead of silently producing a
    Node/tsx command that would fail at startup with
    ``ERR_UNSUPPORTED_ESM_URL_SCHEME`` (B7).

    Args:
        executable: Path to the runtime executable (must be Deno).
        entry_point: Path to the Deno entry point (``main.ts``).
        debug: Enable debug mode (``--inspect``).

    Returns:
        List of command-line arguments.

    Raises:
        RuntimeError: If ``executable`` is not a Deno binary.
    """
    exe_name = Path(executable).name.lower()

    if "deno" not in exe_name:
        raise RuntimeError(
            f"Unsupported JS runtime {executable!r}. Haven JS services "
            "require Deno (npm: specifiers + Deno globals). Node.js/Bun "
            "and tsx/ts-node are not supported (B7)."
        )

    return _get_deno_args(executable, entry_point, debug)


def _get_deno_args(
    executable: str,
    entry_point: Path,
    debug: bool = False
) -> list[str]:
    """Get Deno command-line arguments."""
    args = [
        executable,
        "run",
        # Permissions - all required for browser SDK compatibility
        "--allow-read",
        "--allow-write",
        "--allow-net",
        "--allow-env",
        "--allow-sys",  # Required for Deno.hostname() in browser-shim.ts
    ]

    if debug:
        args.append("--inspect")

    args.append(str(entry_point))

    return args


# Synchronous wrapper for simple use cases

def discover_runtime_sync(preferred: Optional[RuntimeType] = None) -> str:
    """
    Synchronous wrapper for discover_runtime.

    Args:
        preferred: Preferred runtime type (Deno only; see discover_runtime)

    Returns:
        Path to the Deno executable
    """
    return asyncio.run(discover_runtime(preferred))


def check_runtime_available() -> bool:
    """
    Check if the supported JavaScript runtime (Deno) is available.

    Returns:
        True if Deno is available
    """
    try:
        discover_runtime_sync()
        return True
    except RuntimeError:
        return False
