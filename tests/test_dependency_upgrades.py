"""Regression tests for the full dependency upgrade to latest.

Covers:
  - every pin in pyproject.toml uses exact ``==`` comparison
  - croniter 7.0.0 API as used by haven_cli (constructor + ``get_next``,
    invalid expressions raise ``ValueError`` so call sites can report them)
  - Pillow 13.0.0 ``Image.Resampling`` API as used by ``media/frames.py``
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"


def _all_requirement_strings() -> list[str]:
    data = tomllib.loads(_PYPROJECT.read_text())
    reqs: list[str] = list(data["build-system"]["requires"])
    reqs += list(data["project"]["dependencies"])
    for extra in data["project"]["optional-dependencies"].values():
        reqs += list(extra)
    return reqs


def test_all_pins_use_exact_comparison() -> None:
    for req in _all_requirement_strings():
        # Direct references (file:// wheels) and the self-referencing
        # ``all`` extra are exempt from == by definition.
        if " @ " in req or req.startswith("haven-cli["):
            continue
        name_part = req.split(";")[0].strip()
        assert "==" in name_part, f"pin is not exact: {req!r}"
        assert not re.search(r"[<>=!~]", name_part.replace("==", "")), (
            f"pin uses a non-exact specifier: {req!r}"
        )


def test_installed_versions_match_pins_for_upgraded_packages() -> None:
    import importlib.metadata as meta

    expected = {
        "hatchling": "1.33.0",
        "typer": "0.29.0",
        "pydantic": "2.14.3",
        "pydantic-settings": "2.16.2",
        "croniter": "7.0.0",
        "Pillow": "13.0.0",
        "SQLAlchemy": "2.1.8",
        "fastapi": "0.162.2",
        "python-dotenv": "1.2.4",
        "livekit": "1.2.1",
        "av": "19.0.2",
        "python-multipart": "0.0.33",
        "yt-dlp": "2026.10.26",
        "cryptography": "50.0.2",
        "tomli": "2.5.0",
        "pytest": "9.2.0",
        "pytest-asyncio": "1.4.1.post1",
        "mypy": "2.4.1",
        "ruff": "0.17.4",
    }
    for name, want in expected.items():
        assert meta.version(name) == want, (
            f"{name}: want {want}, installed {meta.version(name)}"
        )


def test_croniter_7_get_next() -> None:
    from croniter import croniter  # type: ignore[import-untyped]

    base = datetime(2010, 1, 25, 4, 46, tzinfo=UTC)
    it = croniter("*/5 * * * *", base)
    assert it.get_next(datetime) == datetime(2010, 1, 25, 4, 50, tzinfo=UTC)
    assert it.get_next(datetime) == datetime(2010, 1, 25, 4, 55, tzinfo=UTC)


def test_croniter_7_invalid_raises_value_error() -> None:
    from croniter import croniter  # type: ignore[import-untyped]

    with pytest.raises(ValueError):
        croniter("not a cron")


def test_pillow_13_resampling_lanczos() -> None:
    from PIL import Image

    frame = Image.new("RGB", (32, 32))
    resized = frame.resize((16, 16), Image.Resampling.LANCZOS)
    assert resized.size == (16, 16)


def test_js_services_synapse_core_is_pinned_to_0_11() -> None:
    """The JS bridge must build against synapse-core 0.11.0.

    ``filecoin-pin`` 2.x still depends on ``@filoz/synapse-core@^0.10.0``, so
    npm/Deno install a nested 0.10.3 for the SDK/filecoin-pin runtime path.
    The direct pin (used by ``synapse-wrapper.ts`` and ``probe_providers.ts``)
    must stay on 0.11.0 until filecoin-pin publishes a compatible release.
    """
    js_services = _REPO / "js-services"
    package = json.loads((js_services / "package.json").read_text())
    deno = json.loads((js_services / "deno.json").read_text())
    lock = json.loads((js_services / "package-lock.json").read_text())

    assert package["dependencies"]["@filoz/synapse-core"] == "0.11.0"
    assert deno["imports"]["@filoz/synapse-core"] == "npm:@filoz/synapse-core@0.11.0"
    assert (
        lock["packages"]["node_modules/@filoz/synapse-core"]["version"] == "0.11.0"
    ), "package-lock.json top-level synapse-core pin is stale"
