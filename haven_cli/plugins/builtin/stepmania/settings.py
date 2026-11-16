"""Configuration model for :class:`StepManiaPlugin`.

Settings live in ``[plugins.settings.StepManiaPlugin]`` (or the legacy
``stepmania`` key). Values are deliberately flat so ``haven config`` can
round-trip them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROVIDERS = ("etterna", "stepmania_online")
UNITS = ("pack", "song")

#: Public Etterna Typesense key shipped by the open-source Etterna client.
#: It is not a user secret; ``etterna_api_key`` / ``etterna_api_key_env``
#: can still override it for testing or a future key rotation.
DEFAULT_ETTERNA_API_KEY = "uNVBQbmgvnet2LTpT6sE3XYe7JeD8xej"
DEFAULT_ETTERNA_BASE_URL = "https://search.etternaonline.com"
DEFAULT_SMO_BASE_URL = "https://stepmaniaonline.net"

#: Chart, audio and image files normally found in StepMania song folders.
DEFAULT_INCLUDE_EXTENSIONS = (
    ".sm",
    ".ssc",
    ".smx",
    ".dwi",
    ".ogg",
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".opus",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".webp",
    ".svg",
)

DEFAULT_EXCLUDE_EXTENSIONS = (".zip", ".torrent", ".nfo", ".sfv", ".par2")

GiB = 1024**3


class SettingsError(ValueError):
    """Invalid StepManiaPlugin configuration."""


def _list(value: Any, key: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def _str_list(value: Any, key: str) -> list[str]:
    return [str(item) for item in _list(value, key)]


def _int(value: Any, key: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{key}: expected an integer, got {value!r}") from exc
    if number < minimum:
        raise SettingsError(f"{key}: must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise SettingsError(f"{key}: must be <= {maximum}")
    return number


def _float(value: Any, key: str, *, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{key}: expected a number, got {value!r}") from exc
    if number < minimum:
        raise SettingsError(f"{key}: must be >= {minimum}")
    return number


def _bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1", "on"):
            return True
        if lowered in ("false", "no", "0", "off"):
            return False
    if isinstance(value, int):
        return bool(value)
    raise SettingsError(f"{key}: expected true/false, got {value!r}")


def _choice(value: Any, choices: tuple[str, ...], key: str) -> str:
    text = str(value).strip().lower()
    if text not in choices:
        raise SettingsError(f"{key}: must be one of {', '.join(choices)} (got {value!r})")
    return text


def _path(value: Any, key: str) -> Path:
    return Path(str(value)).expanduser()


def _extensions(value: Any, key: str) -> tuple[str, ...]:
    items = _str_list(value, key)
    normalized: list[str] = []
    for item in items:
        item = item.strip().lower()
        if not item:
            continue
        if item == "*":
            normalized.append("*")
            continue
        normalized.append(item if item.startswith(".") else f".{item}")
    return tuple(dict.fromkeys(normalized))


def _optional_float(value: Any, key: str) -> float | None:
    if value is None or value == "":
        return None
    return _float(value, key)


def version_tuple(value: str) -> tuple[int, ...]:
    """Parse versions like ``Stepmania 3.9`` or ``Stepmania 5`` for comparison."""
    parts: list[int] = []
    for match in re.findall(r"\d+(?:\.\d+)?", value):
        parts.extend(int(float(part)) for part in match.split("."))
    return tuple(parts)


@dataclass
class StepManiaSettings:
    """Validated StepMania plugin settings."""

    providers: tuple[str, ...] = ("stepmania_online",)
    count: int = 1
    unit: str = "pack"
    download_dir: Path = field(
        default_factory=lambda: Path.home() / "haven" / "downloads" / "stepmania"
    )
    state_file: Path | None = None

    timeout_s: float = 60.0
    download_timeout_s: float = 3600.0
    request_interval_s: float = 0.5
    max_download_bytes: int = 4 * GiB
    max_extracted_bytes: int = 8 * GiB
    max_files_per_pack: int = 5000

    extract_archive: bool = True
    keep_zip: bool = False
    # Pack mode: upload the pack ZIP as a single Arkiv entity with a
    # ``stepmania-pack`` manifest (what FeetOnFire's social feed reads),
    # instead of splitting the pack into per-file entities. Takes priority
    # over ``extract_archive``.
    upload_as_pack: bool = True
    # Upper bound on songs listed in the ``x.dance`` manifest (advisory only;
    # the ZIP itself always carries the full content).
    pack_manifest_max_songs: int = 250
    include_extensions: tuple[str, ...] = DEFAULT_INCLUDE_EXTENSIONS
    exclude_extensions: tuple[str, ...] = DEFAULT_EXCLUDE_EXTENSIONS

    catalog_ttl_s: float = 3600.0
    random_seed: int | None = None

    failure_cooldown_s: float = 24 * 3600.0
    max_attempts: int = 5

    generic_file_accept: tuple[str, ...] = ("*",)
    generic_file_reject: tuple[str, ...] = ()
    pipeline_options: dict[str, Any] = field(default_factory=dict)

    etterna_base_url: str = DEFAULT_ETTERNA_BASE_URL
    etterna_api_key: str = DEFAULT_ETTERNA_API_KEY
    etterna_api_key_env: str = "ETTERNA_TYPESENSE_API_KEY"
    etterna_random_attempts: int = 10
    etterna_pack_id_min: int = 1
    etterna_pack_id_max: int = 4600
    etterna_song_id_min: int = 139_000
    etterna_song_id_max: int = 220_000
    etterna_exclude_nsfw: bool = True
    etterna_min_overall: float | None = None
    etterna_max_overall: float | None = None
    etterna_tags: tuple[str, ...] = ()

    smo_base_url: str = DEFAULT_SMO_BASE_URL
    smo_pack_types: tuple[str, ...] = ()
    smo_min_version: str = ""
    smo_max_version: str = ""
    smo_min_size_bytes: int = 0
    smo_max_size_bytes: int = 0

    warnings: list[str] = field(default_factory=list)

    @property
    def state_path(self) -> Path:
        if self.state_file is not None:
            return self.state_file
        return self.download_dir / ".stepmania_state.json"


def load_settings(raw: dict[str, Any] | None) -> StepManiaSettings:
    """Validate raw plugin settings into :class:`StepManiaSettings`."""
    raw = dict(raw or {})
    settings = StepManiaSettings()
    warnings: list[str] = []

    known_keys = {
        "providers",
        "provider",
        "count",
        "unit",
        "download_dir",
        "state_file",
        "timeout_s",
        "download_timeout_s",
        "request_interval_s",
        "max_download_bytes",
        "max_extracted_bytes",
        "max_files_per_pack",
        "extract_archive",
        "keep_zip",
        "upload_as_pack",
        "pack_manifest_max_songs",
        "include_extensions",
        "exclude_extensions",
        "catalog_ttl_s",
        "random_seed",
        "failure_cooldown_s",
        "max_attempts",
        "generic_file_accept",
        "generic_file_reject",
        "pipeline_options",
        "etterna_base_url",
        "etterna_api_key",
        "etterna_api_key_env",
        "etterna_random_attempts",
        "etterna_pack_id_min",
        "etterna_pack_id_max",
        "etterna_song_id_min",
        "etterna_song_id_max",
        "etterna_exclude_nsfw",
        "etterna_min_overall",
        "etterna_max_overall",
        "etterna_tags",
        "smo_base_url",
        "smo_pack_types",
        "smo_min_version",
        "smo_max_version",
        "smo_min_size_bytes",
        "smo_max_size_bytes",
    }
    unknown = sorted(key for key in raw if key not in known_keys)
    if unknown:
        warnings.append(f"ignoring unknown settings: {', '.join(unknown)}")

    providers_raw = raw.get("providers", raw.get("provider"))
    if providers_raw is None:
        providers = settings.providers
    else:
        providers = tuple(
            _choice(item, PROVIDERS, "providers") for item in _str_list(providers_raw, "providers")
        )
        if not providers:
            raise SettingsError("providers: at least one provider is required")
        providers = tuple(dict.fromkeys(providers))

    unit = _choice(raw.get("unit", settings.unit), UNITS, "unit")
    if unit == "song" and providers == ("stepmania_online",):
        raise SettingsError(
            "unit = 'song' requires the 'etterna' provider; "
            "stepmania_online has no bulk song API"
        )

    settings.providers = providers
    settings.unit = unit
    settings.count = _int(raw.get("count", settings.count), "count", minimum=1, maximum=100)
    if "download_dir" in raw:
        settings.download_dir = _path(raw["download_dir"], "download_dir")
    if "state_file" in raw and raw["state_file"]:
        settings.state_file = _path(raw["state_file"], "state_file")

    settings.timeout_s = _float(raw.get("timeout_s", settings.timeout_s), "timeout_s", minimum=0.1)
    settings.download_timeout_s = _float(
        raw.get("download_timeout_s", settings.download_timeout_s),
        "download_timeout_s",
        minimum=1.0,
    )
    settings.request_interval_s = _float(
        raw.get("request_interval_s", settings.request_interval_s),
        "request_interval_s",
    )
    settings.max_download_bytes = _int(
        raw.get("max_download_bytes", settings.max_download_bytes),
        "max_download_bytes",
        minimum=1024,
    )
    settings.max_extracted_bytes = _int(
        raw.get("max_extracted_bytes", settings.max_extracted_bytes),
        "max_extracted_bytes",
        minimum=1024,
    )
    settings.max_files_per_pack = _int(
        raw.get("max_files_per_pack", settings.max_files_per_pack),
        "max_files_per_pack",
        minimum=1,
        maximum=100_000,
    )
    settings.extract_archive = _bool(
        raw.get("extract_archive", settings.extract_archive), "extract_archive"
    )
    settings.keep_zip = _bool(raw.get("keep_zip", settings.keep_zip), "keep_zip")
    settings.upload_as_pack = _bool(
        raw.get("upload_as_pack", settings.upload_as_pack), "upload_as_pack"
    )
    settings.pack_manifest_max_songs = _int(
        raw.get("pack_manifest_max_songs", settings.pack_manifest_max_songs),
        "pack_manifest_max_songs",
        minimum=0,
        maximum=5000,
    )

    if "include_extensions" in raw:
        settings.include_extensions = _extensions(raw["include_extensions"], "include_extensions")
    if "exclude_extensions" in raw:
        settings.exclude_extensions = _extensions(
            raw["exclude_extensions"], "exclude_extensions"
        )

    settings.catalog_ttl_s = _float(
        raw.get("catalog_ttl_s", settings.catalog_ttl_s), "catalog_ttl_s"
    )
    if raw.get("random_seed") not in (None, ""):
        settings.random_seed = _int(raw["random_seed"], "random_seed", minimum=0)

    settings.failure_cooldown_s = _float(
        raw.get("failure_cooldown_s", settings.failure_cooldown_s),
        "failure_cooldown_s",
    )
    settings.max_attempts = _int(
        raw.get("max_attempts", settings.max_attempts), "max_attempts", minimum=1, maximum=100
    )

    settings.generic_file_accept = tuple(
        _str_list(
            raw.get("generic_file_accept", settings.generic_file_accept), "generic_file_accept"
        )
    )
    settings.generic_file_reject = tuple(
        _str_list(
            raw.get("generic_file_reject", settings.generic_file_reject), "generic_file_reject"
        )
    )
    pipeline_options = raw.get("pipeline_options") or {}
    if not isinstance(pipeline_options, dict):
        raise SettingsError("pipeline_options must be a table")
    settings.pipeline_options = dict(pipeline_options)

    settings.etterna_base_url = str(
        raw.get("etterna_base_url", settings.etterna_base_url)
    ).rstrip("/")
    settings.etterna_api_key = str(raw.get("etterna_api_key") or "").strip()
    settings.etterna_api_key_env = str(
        raw.get("etterna_api_key_env", settings.etterna_api_key_env)
    ).strip()
    if not settings.etterna_api_key:
        settings.etterna_api_key = os.environ.get(settings.etterna_api_key_env, "")
    if not settings.etterna_api_key:
        settings.etterna_api_key = DEFAULT_ETTERNA_API_KEY
    settings.etterna_random_attempts = _int(
        raw.get("etterna_random_attempts", settings.etterna_random_attempts),
        "etterna_random_attempts",
        minimum=1,
        maximum=100,
    )
    settings.etterna_pack_id_min = _int(
        raw.get("etterna_pack_id_min", settings.etterna_pack_id_min),
        "etterna_pack_id_min",
        minimum=1,
    )
    settings.etterna_pack_id_max = _int(
        raw.get("etterna_pack_id_max", settings.etterna_pack_id_max),
        "etterna_pack_id_max",
        minimum=1,
    )
    if settings.etterna_pack_id_min > settings.etterna_pack_id_max:
        raise SettingsError("etterna_pack_id_min must be <= etterna_pack_id_max")
    settings.etterna_song_id_min = _int(
        raw.get("etterna_song_id_min", settings.etterna_song_id_min),
        "etterna_song_id_min",
        minimum=1,
    )
    settings.etterna_song_id_max = _int(
        raw.get("etterna_song_id_max", settings.etterna_song_id_max),
        "etterna_song_id_max",
        minimum=1,
    )
    if settings.etterna_song_id_min > settings.etterna_song_id_max:
        raise SettingsError("etterna_song_id_min must be <= etterna_song_id_max")
    settings.etterna_exclude_nsfw = _bool(
        raw.get("etterna_exclude_nsfw", settings.etterna_exclude_nsfw),
        "etterna_exclude_nsfw",
    )
    settings.etterna_min_overall = _optional_float(
        raw.get("etterna_min_overall"), "etterna_min_overall"
    )
    settings.etterna_max_overall = _optional_float(
        raw.get("etterna_max_overall"), "etterna_max_overall"
    )
    settings.etterna_tags = tuple(
        _str_list(raw.get("etterna_tags", settings.etterna_tags), "etterna_tags")
    )

    settings.smo_base_url = str(raw.get("smo_base_url", settings.smo_base_url)).rstrip("/")
    settings.smo_pack_types = tuple(
        item.lower()
        for item in _str_list(
            raw.get("smo_pack_types", settings.smo_pack_types), "smo_pack_types"
        )
    )
    settings.smo_min_version = str(raw.get("smo_min_version", settings.smo_min_version)).strip()
    settings.smo_max_version = str(raw.get("smo_max_version", settings.smo_max_version)).strip()
    settings.smo_min_size_bytes = _int(
        raw.get("smo_min_size_bytes", settings.smo_min_size_bytes), "smo_min_size_bytes"
    )
    settings.smo_max_size_bytes = _int(
        raw.get("smo_max_size_bytes", settings.smo_max_size_bytes), "smo_max_size_bytes"
    )

    settings.warnings = warnings
    return settings
