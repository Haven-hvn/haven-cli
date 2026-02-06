"""
JS Runtime Discovery.

Auto-detects available JavaScript runtimes (Deno, Node.js) and
provides appropriate command-line arguments for each.
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
    """Supported JavaScript runtime types."""
    
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


# Runtime detection order (preferred first)
RUNTIME_PREFERENCE = [
    RuntimeType.DENO,
    RuntimeType.BUN,
    RuntimeType.NODE,
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
    Discover an available JavaScript runtime.
    
    Args:
        preferred: Preferred runtime type (if available)
    
    Returns:
        Path to the runtime executable
    
    Raises:
        RuntimeError: If no suitable runtime is found
    """
    # Build search order
    search_order = list(RUNTIME_PREFERENCE)
    if preferred and preferred in search_order:
        search_order.remove(preferred)
        search_order.insert(0, preferred)
    
    # Search for runtimes
    for runtime_type in search_order:
        info = await _detect_runtime(runtime_type)
        if info:
            logger.info(f"Discovered runtime: {info.display_name}")
            return info.executable
    
    raise RuntimeError(
        "No JavaScript runtime found. Please install Deno, Node.js, or Bun.\n"
        "Recommended: Install Deno from https://deno.land"
    )


async def discover_all_runtimes() -> list[RuntimeInfo]:
    """
    Discover all available JavaScript runtimes.
    
    Returns:
        List of discovered runtime information
    """
    runtimes = []
    
    for runtime_type in RuntimeType:
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
    
    Args:
        executable: Path to the runtime executable
        entry_point: Path to the entry point script
        debug: Enable debug mode
    
    Returns:
        List of command-line arguments
    """
    # Detect runtime type from executable name
    exe_name = Path(executable).name.lower()
    
    if "deno" in exe_name:
        return _get_deno_args(executable, entry_point, debug)
    elif "bun" in exe_name:
        return _get_bun_args(executable, entry_point, debug)
    else:
        # Assume Node.js
        return _get_node_args(executable, entry_point, debug)


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


def _get_node_args(
    executable: str,
    entry_point: Path,
    debug: bool = False
) -> list[str]:
    """Get Node.js command-line arguments."""
    args = [executable]
    
    # Node needs ts-node or similar for TypeScript
    # For now, assume the entry point is compiled JS or use tsx
    if entry_point.suffix == ".ts":
        # Try to use tsx for TypeScript execution
        tsx_path = shutil.which("tsx")
        if tsx_path:
            args = [tsx_path]
        else:
            # Fall back to ts-node
            ts_node_path = shutil.which("ts-node")
            if ts_node_path:
                args = [ts_node_path]
            else:
                logger.warning(
                    "TypeScript entry point requires tsx or ts-node. "
                    "Install with: npm install -g tsx"
                )
    
    if debug:
        args.append("--inspect")
    
    # Enable ES modules
    args.extend(["--experimental-specifier-resolution=node"])
    
    args.append(str(entry_point))
    
    return args


def _get_bun_args(
    executable: str,
    entry_point: Path,
    debug: bool = False
) -> list[str]:
    """Get Bun command-line arguments."""
    args = [executable, "run"]
    
    if debug:
        args.append("--inspect")
    
    args.append(str(entry_point))
    
    return args


# Synchronous wrapper for simple use cases

def discover_runtime_sync(preferred: Optional[RuntimeType] = None) -> str:
    """
    Synchronous wrapper for discover_runtime.
    
    Args:
        preferred: Preferred runtime type
    
    Returns:
        Path to the runtime executable
    """
    return asyncio.run(discover_runtime(preferred))


def check_runtime_available() -> bool:
    """
    Check if any JavaScript runtime is available.
    
    Returns:
        True if a runtime is available
    """
    try:
        discover_runtime_sync()
        return True
    except RuntimeError:
        return False
