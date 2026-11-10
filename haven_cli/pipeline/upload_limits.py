"""Single source of truth for the Filecoin upload size limit (plan item 6).

The binding limit is ``MAX_UPLOAD_SIZE`` (and the encryption / CAR / safety
overhead factors) in ``js-services/synapse-wrapper.ts``. The wrapper rejects
a file when its *projected encrypted + CAR* size exceeds that constant
(``calculateProjectedSize`` / ``validateFileSize``). Uploading is slow
(2–15 min), so we must refuse oversize files at ingest — before download
bandwidth and encrypt time are spent — using the exact same numbers.

To avoid maintaining two copies of the constants, Python derives the
maximum *plaintext* file size by parsing the wrapper's ``const``
declarations at import time. If the wrapper is unavailable (e.g. a wheel
install without ``js-services/`), we fall back to the same literals.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict

# Mirrors js-services/synapse-wrapper.ts. Used only when the TS file cannot
# be read (installed wheel layout). Keep in sync with the wrapper.
_FALLBACK: Dict[str, float] = {
    "MAX_UPLOAD_SIZE": 1_065_353_216,        # ~1 GiB, Synapse SDK hard limit
    "ENCRYPTION_OVERHEAD_FACTOR": 1.35,      # base64 (~35%)
    "CAR_OVERHEAD_FACTOR": 1.01,            # CAR framing (~1%)
    "SAFETY_MARGIN": 1.05,                  # extra 5%
}

_CONST_RE = re.compile(
    r"\bconst\s+(MAX_UPLOAD_SIZE|ENCRYPTION_OVERHEAD_FACTOR|"
    r"CAR_OVERHEAD_FACTOR|SAFETY_MARGIN)\s*=\s*([0-9_]+(?:\.[0-9]+)?)\s*;"
)


def _wrapper_path() -> Path:
    # haven_cli/pipeline/upload_limits.py -> repo root is parents[2].
    return Path(__file__).resolve().parents[2] / "js-services" / "synapse-wrapper.ts"


@lru_cache(maxsize=1)
def _constants() -> Dict[str, float]:
    """Read the size constants from the TS wrapper, with safe fallbacks."""
    values = dict(_FALLBACK)
    try:
        text = _wrapper_path().read_text(encoding="utf-8")
        for name, raw in _CONST_RE.findall(text):
            try:
                values[name] = float(raw.replace("_", ""))
            except ValueError:
                continue
    except OSError:
        # Wheel install without js-services — use the mirrored literals.
        pass
    return values


def max_upload_size() -> int:
    """The Synapse SDK hard cap on the projected (encrypted+CAR) upload."""
    return int(_constants()["MAX_UPLOAD_SIZE"])


def projected_size(plaintext_size: int, encryption_enabled: bool = True) -> int:
    """Reproduce ``SynapseWrapper.calculateProjectedSize`` exactly."""
    c = _constants()
    projected = float(plaintext_size)
    if encryption_enabled:
        projected = math.ceil(projected * c["ENCRYPTION_OVERHEAD_FACTOR"])
    projected = math.ceil(projected * c["CAR_OVERHEAD_FACTOR"])
    projected = math.ceil(projected * c["SAFETY_MARGIN"])
    return int(projected)


def max_plaintext_size(encryption_enabled: bool = True) -> int:
    """Largest *plaintext* file whose projected upload still fits the cap.

    Mirrors ``SynapseWrapper.getMaxFileSize``: divides the hard cap by the
    combined overhead factors. Encryption is enabled by default because
    Haven-AOL v3 is the default (``PipelineConfig.encryption_enabled=True``).
    """
    c = _constants()
    total_overhead = c["CAR_OVERHEAD_FACTOR"] * c["SAFETY_MARGIN"]
    if encryption_enabled:
        total_overhead *= c["ENCRYPTION_OVERHEAD_FACTOR"]
    candidate = math.floor(c["MAX_UPLOAD_SIZE"] / total_overhead)
    # ``calculateProjectedSize`` uses Math.ceil at each step, so the plain
    # floor division can overshoot the cap by a byte or two. Walk down until
    # the *actual* projected size (the value validateFileSize compares)
    # fits. This guarantees the ingest gate never admits a file the wrapper
    # would later reject.
    cap = int(c["MAX_UPLOAD_SIZE"])
    while candidate > 0 and projected_size(candidate, encryption_enabled) > cap:
        candidate -= 1
    return candidate
