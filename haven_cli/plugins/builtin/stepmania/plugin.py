"""StepMania/Etterna random pack archiver plugin.

The plugin implements the random-selection strategy from the StepMania
Random Selection API spec:

* ``etterna`` draws random song/pack IDs against the public Typesense API.
* ``stepmania_online`` downloads the SMO CSV catalog and samples locally.

Pack ZIPs are downloaded with the guarded HTTP fetcher, extracted with
path-sanitizing ZIP handling, and the song content files are returned as
``output_paths`` so the scheduler can feed each file through Haven's
generic-file pipeline.
"""

from __future__ import annotations

import json
import logging
import random
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

import httpx

from haven_cli.acquisition.http_fetch import (
    FetchError,
    FetchPolicy,
    fetch_to_file,
    safe_filename,
)
from haven_cli.plugins.base import (
    ArchiverPlugin,
    ArchiveResult,
    MediaSource,
    PluginCapability,
    PluginInfo,
)
from haven_cli.plugins.builtin.stepmania.providers import (
    PackCandidate,
    ProviderError,
    provider_for,
)
from haven_cli.plugins.builtin.stepmania.settings import (
    StepManiaSettings,
    SettingsError,
    load_settings,
)

logger = logging.getLogger(__name__)

MEDIA_TYPE = "stepmania"
_STATE_VERSION = 1


class StepManiaError(RuntimeError):
    """Non-provider StepMania plugin failure."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class StepManiaPlugin(ArchiverPlugin):
    """Discover random StepMania packs and archive their song files."""

    #: Optional httpx transport used for API and download requests.
    http_transport: Any = None

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._settings_cache: Optional[StepManiaSettings] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._download_dir = Path("downloads") / "stepmania"
        self._state_path = self._download_dir / ".stepmania_state.json"
        self._state_cache: Optional[Dict[str, Any]] = None

    # ── configuration ────────────────────────────────────────────────────

    def _raw_settings(self) -> Dict[str, Any]:
        raw = self._config if isinstance(self._config, dict) else {}
        wrapped = "plugins" in raw or "plugin_settings" in raw

        plugins = raw.get("plugins", raw)
        if hasattr(plugins, "plugin_settings"):
            table = plugins.plugin_settings
        elif isinstance(plugins, dict) and isinstance(plugins.get("plugin_settings"), dict):
            table = plugins["plugin_settings"]
        elif isinstance(plugins, dict):
            table = plugins
        elif isinstance(raw.get("plugin_settings"), dict):
            table = raw["plugin_settings"]
        else:
            table = {}

        if not isinstance(table, dict):
            return {}
        nested = table.get("StepManiaPlugin") or table.get("stepmania")
        if isinstance(nested, dict):
            return nested
        # A wrapped plugins/plugin_settings table belongs to all plugins; only
        # return it when it is actually this plugin's direct settings dict.
        return {} if wrapped else table

    def settings(self) -> StepManiaSettings:
        if self._settings_cache is None:
            self._settings_cache = load_settings(self._raw_settings())
        return self._settings_cache

    def configure(self, config: Dict[str, Any]) -> None:
        super().configure(config)
        self._settings_cache = None
        self._state_cache = None

    def validate_config(self) -> List[str]:
        try:
            settings = self.settings()
        except SettingsError as exc:
            return [str(exc)]
        return list(settings.warnings)

    def _settings_for_options(self, options: Dict[str, Any]) -> StepManiaSettings:
        raw = dict(self._raw_settings())
        if "stepmania_provider" in options:
            raw["provider"] = options["stepmania_provider"]
        if "stepmania_providers" in options:
            raw["providers"] = options["stepmania_providers"]
        if "stepmania_count" in options:
            raw["count"] = options["stepmania_count"]
        if "stepmania_unit" in options:
            raw["unit"] = options["stepmania_unit"]
        if "stepmania_seed" in options:
            raw["random_seed"] = options["stepmania_seed"]
        return load_settings(raw)

    @classmethod
    def from_haven_config(cls) -> "StepManiaPlugin":
        from haven_cli.config import get_config

        config = get_config()
        plugin_settings = config.plugins.plugin_settings
        settings = plugin_settings.get("StepManiaPlugin") or plugin_settings.get("stepmania", {})
        return cls(config=settings if isinstance(settings, dict) else {})

    @classmethod
    def validate_job_options(cls, options: Dict[str, Any]) -> List[str]:
        """Validate ``stepmania_*`` job options for ``haven jobs create``."""
        if not any(str(key).startswith("stepmania_") for key in options):
            return []
        try:
            cls.from_haven_config()._settings_for_options(options)
        except (SettingsError, TypeError, ValueError) as exc:
            return [str(exc)]
        return []

    # ── plugin metadata/lifecycle ────────────────────────────────────────

    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name="StepManiaPlugin",
            display_name="StepMania Archiver",
            version="1.0.0",
            description=(
                "Randomly discovers StepMania/Etterna packs and archives "
                "their song files through the Haven pipeline."
            ),
            author="Haven Team",
            media_types=[
                MEDIA_TYPE,
                "application/zip",
                "archive",
                "text",
                "audio",
                "image",
                "other",
            ],
            capabilities=[
                PluginCapability.DISCOVER,
                PluginCapability.ARCHIVE,
                PluginCapability.METADATA,
                PluginCapability.HEALTH_CHECK,
            ],
            config_schema={
                "type": "object",
                "properties": {
                    "providers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["etterna", "stepmania_online"]},
                        "default": ["stepmania_online"],
                    },
                    "count": {"type": "integer", "default": 1, "minimum": 1, "maximum": 100},
                    "unit": {"type": "string", "enum": ["pack", "song"], "default": "pack"},
                    "download_dir": {"type": "string"},
                    "extract_archive": {"type": "boolean", "default": True},
                    "keep_zip": {"type": "boolean", "default": False},
                    "include_extensions": {"type": "array", "items": {"type": "string"}},
                    "max_download_bytes": {"type": "integer"},
                    "max_extracted_bytes": {"type": "integer"},
                    "max_files_per_pack": {"type": "integer"},
                    "etterna_min_overall": {"type": "number"},
                    "etterna_max_overall": {"type": "number"},
                    "etterna_tags": {"type": "array", "items": {"type": "string"}},
                    "smo_pack_types": {"type": "array", "items": {"type": "string"}},
                    "smo_min_version": {"type": "string"},
                    "smo_max_version": {"type": "string"},
                    "pipeline_options": {"type": "object"},
                },
            },
        )

    async def initialize(self) -> None:
        settings = self.settings()
        settings.download_dir = settings.download_dir.expanduser()
        self._download_dir = settings.download_dir.resolve()
        self._download_dir.mkdir(parents=True, exist_ok=True)
        (self._download_dir / ".tmp").mkdir(parents=True, exist_ok=True)

        if settings.state_file is not None:
            state_path = settings.state_file.expanduser()
            if not state_path.is_absolute():
                state_path = (self._download_dir / state_path).resolve()
        else:
            state_path = self._download_dir / ".stepmania_state.json"
        self._state_path = state_path
        self._state_cache = None

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.timeout_s),
                transport=self.http_transport,
                headers={
                    "User-Agent": "HavenCLI-StepManiaPlugin/1.0",
                    "Accept": "application/json",
                },
            )
        await super().initialize()
        logger.info(
            "StepManiaPlugin initialized (providers=%s, count=%d, unit=%s)",
            ",".join(settings.providers),
            settings.count,
            settings.unit,
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await super().shutdown()

    async def health_check(self) -> bool:
        if not self._initialized or self._client is None:
            return False
        if not self._download_dir.exists():
            return False

        settings = self.settings()
        for provider_name in settings.providers:
            try:
                if provider_name == "etterna":
                    response = await self._client.get(f"{settings.etterna_base_url}/health")
                    if response.status_code != 200:
                        logger.error("Etterna health check returned HTTP %s", response.status_code)
                        return False
                elif provider_name == "stepmania_online":
                    async with self._client.stream("GET", settings.smo_base_url) as response:
                        if response.status_code >= 400:
                            logger.error(
                                "SMO health check returned HTTP %s", response.status_code
                            )
                            return False
            except Exception as exc:
                logger.error("StepMania provider %s health check failed: %s", provider_name, exc)
                return False
        return self._enabled

    # ── discovery ────────────────────────────────────────────────────────

    async def discover_sources(self) -> List[MediaSource]:
        return await self.discover_sources_for({})

    async def discover_sources_for(self, options: Dict[str, Any]) -> List[MediaSource]:
        if not self._initialized or self._client is None:
            logger.error("StepManiaPlugin not initialized")
            return []

        settings = self._settings_for_options(options or {})
        if settings.unit == "song" and settings.providers == ("stepmania_online",):
            raise SettingsError(
                "unit = 'song' requires the 'etterna' provider; "
                "stepmania_online has no bulk song API"
            )

        self._state_cache = None
        if settings.random_seed is not None:
            rng = random.Random(settings.random_seed)
        else:
            rng = random.Random()
        sources: List[MediaSource] = []

        for provider_name in settings.providers:
            try:
                provider = provider_for(provider_name, settings, self._client)
                candidates = await provider.discover(
                    count=settings.count,
                    unit=settings.unit,
                    rng=rng,
                    unavailable=lambda candidate: self._candidate_unavailable(candidate, settings),
                )
            except ProviderError as exc:
                logger.error("StepMania provider %s discovery failed: %s", provider_name, exc)
                continue
            except Exception as exc:
                logger.error("StepMania provider %s discovery error: %s", provider_name, exc)
                continue

            for candidate in candidates:
                sources.append(self._to_source(candidate, settings))

        logger.info("StepMania discovery returned %d pack source(s)", len(sources))
        return sources

    def _to_source(self, candidate: PackCandidate, settings: StepManiaSettings) -> MediaSource:
        candidate_data = candidate.to_dict()
        metadata: Dict[str, Any] = {
            **candidate.metadata,
            "stepmania_provider": candidate.provider,
            "stepmania_pack_id": candidate.pack_id,
            "stepmania_pack_name": candidate.name,
            "stepmania_candidate": candidate_data,
            "song_count": candidate.song_count,
            "size_bytes": candidate.size_bytes,
            "generic_files_enabled": True,
            "generic_file_accept": list(settings.generic_file_accept),
            "generic_file_reject": list(settings.generic_file_reject),
        }
        return MediaSource(
            id=candidate.source_id,
            media_type=MEDIA_TYPE,
            uri=candidate.download_url,
            title=candidate.name,
            metadata=metadata,
        )

    # ── state/deduplication ──────────────────────────────────────────────

    def _candidate_dirs(self, candidate: PackCandidate) -> List[Path]:
        safe_name = safe_filename(candidate.name, default=f"pack-{candidate.pack_id}")
        provider_dir = self._download_dir / candidate.provider
        primary = provider_dir / safe_name
        alternate = primary.with_name(f"{primary.name} ({candidate.pack_id})")
        return [primary, alternate] if alternate != primary else [primary]

    def _read_marker(self, pack_dir: Path) -> Optional[Dict[str, Any]]:
        marker_path = pack_dir / ".haven-stepmania.json"
        if not marker_path.exists():
            return None
        try:
            data = json.loads(marker_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read StepMania marker %s: %s", marker_path, exc)
            return None

    def _write_marker(
        self,
        pack_dir: Path,
        candidate: PackCandidate,
        output_paths: List[str],
        output_titles: Dict[str, str],
    ) -> None:
        marker = {
            "version": _STATE_VERSION,
            "provider": candidate.provider,
            "pack_id": candidate.pack_id,
            "name": candidate.name,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "output_paths": list(output_paths),
            "output_titles": dict(output_titles),
        }
        try:
            (pack_dir / ".haven-stepmania.json").write_text(
                json.dumps(marker, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Could not write StepMania marker for %s: %s", pack_dir, exc)

    def _marker_matches(self, marker: Optional[Dict[str, Any]], candidate: PackCandidate) -> bool:
        return bool(
            marker
            and marker.get("provider") == candidate.provider
            and str(marker.get("pack_id")) == candidate.pack_id
        )

    def _is_downloaded(self, candidate: PackCandidate) -> bool:
        for pack_dir in self._candidate_dirs(candidate):
            if self._marker_matches(self._read_marker(pack_dir), candidate):
                return True
            if not pack_dir.exists():
                continue
            try:
                if any(pack_dir.iterdir()):
                    return True
            except OSError:
                continue
        return False

    def _load_state(self) -> Dict[str, Any]:
        if self._state_cache is not None:
            return self._state_cache
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._state_cache = data
                return self._state_cache
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read StepMania state %s: %s", self._state_path, exc)
        self._state_cache = {"version": _STATE_VERSION, "failed": {}}
        return self._state_cache

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(self._load_state(), indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Could not write StepMania state %s: %s", self._state_path, exc)

    @staticmethod
    def _state_key(candidate: PackCandidate) -> str:
        return f"{candidate.provider}:{candidate.pack_id}"

    def _is_failed(self, candidate: PackCandidate, settings: StepManiaSettings) -> bool:
        failed = self._load_state().get("failed")
        if not isinstance(failed, dict):
            return False
        entry = failed.get(self._state_key(candidate))
        if not isinstance(entry, dict):
            return False
        if entry.get("permanent"):
            return True
        try:
            next_attempt = float(entry.get("next_attempt_at") or 0)
        except (TypeError, ValueError):
            return False
        return next_attempt > time.time()

    def _candidate_unavailable(
        self, candidate: PackCandidate, settings: StepManiaSettings
    ) -> bool:
        return self._is_downloaded(candidate) or self._is_failed(candidate, settings)

    def _record_failure(
        self,
        candidate: PackCandidate,
        settings: StepManiaSettings,
        error: str,
        *,
        permanent: bool = False,
    ) -> None:
        state = self._load_state()
        failed = state.get("failed")
        if not isinstance(failed, dict):
            failed = {}
            state["failed"] = failed
        key = self._state_key(candidate)
        previous = failed.get(key) if isinstance(failed.get(key), dict) else {}
        attempts = int(previous.get("attempts") or 0) + 1
        permanent = permanent or attempts >= settings.max_attempts
        failed[key] = {
            "provider": candidate.provider,
            "pack_id": candidate.pack_id,
            "name": candidate.name,
            "attempts": attempts,
            "last_error": error[:1000],
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "next_attempt_at": time.time() + settings.failure_cooldown_s,
            "permanent": permanent,
        }
        self._save_state()

    def _record_success(self, candidate: PackCandidate) -> None:
        state = self._load_state()
        failed = state.get("failed")
        if isinstance(failed, dict) and failed.pop(self._state_key(candidate), None) is not None:
            self._save_state()

    # ── archive ──────────────────────────────────────────────────────────

    async def archive(self, source: MediaSource) -> ArchiveResult:
        if source.media_type != MEDIA_TYPE:
            return ArchiveResult(
                success=False,
                error=f"Unsupported media type: {source.media_type}",
            )
        if not self._initialized or self._client is None:
            return ArchiveResult(success=False, error="Plugin not initialized")

        try:
            candidate = self._candidate_from_source(source)
        except (KeyError, TypeError, ValueError) as exc:
            return ArchiveResult(success=False, error=f"Invalid StepMania source: {exc}")
        if candidate.provider not in ("etterna", "stepmania_online") or not candidate.pack_id:
            return ArchiveResult(success=False, error="Invalid StepMania provider or pack ID")

        settings = self.settings()
        candidate_dirs = self._candidate_dirs(candidate)
        for pack_dir in candidate_dirs:
            marker = self._read_marker(pack_dir)
            if self._marker_matches(marker, candidate):
                return self._cached_result(source, candidate, pack_dir, marker or {}, settings)

        pack_dir = self._select_pack_dir(candidate_dirs)
        try:
            result = await self._archive_candidate(source, candidate, pack_dir, settings)
        except Exception as exc:
            permanent = bool(getattr(exc, "permanent", False))
            self._record_failure(candidate, settings, str(exc), permanent=permanent)
            logger.error("StepMania archive failed for %s: %s", candidate.source_id, exc)
            return ArchiveResult(success=False, error=str(exc))

        self._record_success(candidate)
        return result

    def _candidate_from_source(self, source: MediaSource) -> PackCandidate:
        metadata = source.metadata or {}
        raw = metadata.get("stepmania_candidate")
        if isinstance(raw, dict):
            return PackCandidate.from_dict(raw)
        return PackCandidate(
            provider=str(metadata.get("stepmania_provider") or ""),
            pack_id=str(metadata.get("stepmania_pack_id") or ""),
            name=str(metadata.get("stepmania_pack_name") or source.title or ""),
            download_url=str(metadata.get("download_url") or source.uri),
            song_count=int(metadata.get("song_count") or 0),
            size_bytes=int(metadata.get("size_bytes") or 0),
            metadata=dict(metadata),
        )

    def _select_pack_dir(self, candidate_dirs: List[Path]) -> Path:
        for pack_dir in candidate_dirs:
            if not pack_dir.exists():
                return pack_dir
            try:
                if not any(pack_dir.iterdir()):
                    return pack_dir
            except OSError:
                return pack_dir
        return candidate_dirs[0]

    async def _archive_candidate(
        self,
        source: MediaSource,
        candidate: PackCandidate,
        pack_dir: Path,
        settings: StepManiaSettings,
    ) -> ArchiveResult:
        pack_dir.mkdir(parents=True, exist_ok=True)
        zip_path = await self._download_zip(candidate, settings)

        try:
            if not settings.extract_archive:
                archive_path = self._move_zip_into_pack(zip_path, candidate, pack_dir)
                output_paths = [str(archive_path)]
                output_titles = {str(archive_path): candidate.name}
                file_size = archive_path.stat().st_size
            else:
                extract_root = Path(
                    tempfile.mkdtemp(prefix=f".{pack_dir.name}-extract-", dir=pack_dir.parent)
                )
                try:
                    self._extract_zip(zip_path, extract_root, settings)
                    selected = self._select_content_files(extract_root, settings)
                    if not selected:
                        raise StepManiaError(
                            f"No StepMania content files found in {candidate.name}", permanent=True
                        )
                    if len(selected) > settings.max_files_per_pack:
                        raise StepManiaError(
                            f"Pack contains {len(selected)} files, over max_files_per_pack="
                            f"{settings.max_files_per_pack}",
                            permanent=True,
                        )
                    rel_paths = [path.relative_to(extract_root) for path in selected]
                    moved = self._move_extracted_files(extract_root, pack_dir)
                    missing = [rel for rel in rel_paths if rel not in moved]
                    if missing:
                        raise StepManiaError(
                            f"Could not move extracted files into {pack_dir.name}", permanent=True
                        )
                    output_files = [moved[rel] for rel in rel_paths]
                finally:
                    shutil.rmtree(extract_root, ignore_errors=True)
                output_paths = [str(path.resolve()) for path in output_files]
                output_titles = self._output_titles(pack_dir, candidate, output_files)
                file_size = sum(path.stat().st_size for path in output_files)
                self._retain_or_delete_zip(zip_path, candidate, settings)
        except Exception:
            zip_path.unlink(missing_ok=True)
            raise

        self._write_marker(pack_dir, candidate, output_paths, output_titles)
        return ArchiveResult(
            success=True,
            output_path=output_paths[0],
            file_size=file_size,
            metadata=self._result_metadata(
                source,
                candidate,
                settings,
                output_paths,
                output_titles,
            ),
        )

    async def _download_zip(
        self, candidate: PackCandidate, settings: StepManiaSettings
    ) -> Path:
        staging_dir = self._download_dir / ".tmp"
        staging_dir.mkdir(parents=True, exist_ok=True)
        safe_name = safe_filename(candidate.name, default=f"pack-{candidate.pack_id}")
        policy = FetchPolicy(
            max_bytes=settings.max_download_bytes,
            timeout=settings.download_timeout_s,
            user_agent="HavenCLI-StepManiaPlugin/1.0",
            transport=self.http_transport,
        )
        try:
            fetched = await fetch_to_file(
                candidate.download_url,
                staging_dir,
                policy,
                filename_hint=f"{safe_name}-{candidate.pack_id}.zip",
                min_interval=settings.request_interval_s,
            )
        except FetchError as exc:
            raise StepManiaError(str(exc), permanent=exc.permanent) from exc
        if isinstance(fetched, str):
            raise StepManiaError(
                f"StepMania download redirected to unsupported URI for {candidate.name}",
                permanent=True,
            )
        if fetched.mime != "application/zip" and not fetched.path.suffix.lower().endswith(".zip"):
            raise StepManiaError(
                f"StepMania download was not a ZIP archive (MIME {fetched.mime})",
                permanent=True,
            )
        return fetched.path

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        for index in range(1, 10_000):
            candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
            if not candidate.exists():
                return candidate
        raise StepManiaError(f"cannot find a free path near {path}", permanent=True)

    def _move_zip_into_pack(
        self, zip_path: Path, candidate: PackCandidate, pack_dir: Path
    ) -> Path:
        safe_name = safe_filename(candidate.name, default=f"pack-{candidate.pack_id}")
        target = self._unique_path(pack_dir / f"{safe_name}-{candidate.pack_id}.zip")
        try:
            zip_path.replace(target)
        except OSError:
            shutil.copy2(zip_path, target)
            zip_path.unlink(missing_ok=True)
        return target

    def _retain_or_delete_zip(
        self, zip_path: Path, candidate: PackCandidate, settings: StepManiaSettings
    ) -> None:
        if settings.keep_zip:
            archive_dir = self._download_dir / "archives" / candidate.provider
            archive_dir.mkdir(parents=True, exist_ok=True)
            safe_name = safe_filename(candidate.name, default=f"pack-{candidate.pack_id}")
            target = self._unique_path(archive_dir / f"{safe_name}-{candidate.pack_id}.zip")
            try:
                zip_path.replace(target)
            except OSError:
                shutil.copy2(zip_path, target)
                zip_path.unlink(missing_ok=True)
            return
        zip_path.unlink(missing_ok=True)

    @staticmethod
    def _safe_zip_parts(filename: str) -> List[str]:
        normalized = filename.replace("\\", "/")
        parts = [
            part
            for part in PurePosixPath(normalized).parts
            if part not in ("", ".", "..")
        ]
        safe_parts: List[str] = []
        for index, part in enumerate(parts):
            cleaned = safe_filename(part, default=f"part-{index}")
            if cleaned in (".", ".."):
                cleaned = f"part-{index}"
            safe_parts.append(cleaned)
        return safe_parts

    def _extract_zip(
        self, zip_path: Path, pack_dir: Path, settings: StepManiaSettings
    ) -> None:
        if not zipfile.is_zipfile(zip_path):
            raise StepManiaError("Downloaded StepMania pack is not a valid ZIP", permanent=True)

        pack_root = pack_dir.resolve()
        extracted_bytes = 0
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.filename.endswith("/"):
                    continue
                if info.flag_bits & 0x1:
                    raise StepManiaError(
                        f"Encrypted ZIP entry is not supported: {info.filename}",
                        permanent=True,
                    )
                parts = self._safe_zip_parts(info.filename)
                if not parts:
                    continue
                target = pack_dir.joinpath(*parts)
                try:
                    if not target.resolve().is_relative_to(pack_root):
                        raise StepManiaError(
                            f"Unsafe ZIP path rejected: {info.filename}", permanent=True
                        )
                except OSError as exc:
                    raise StepManiaError(
                        f"Could not resolve ZIP path {info.filename}: {exc}", permanent=True
                    ) from exc

                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(info) as source, open(target, "wb") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        extracted_bytes += len(chunk)
                        if extracted_bytes > settings.max_extracted_bytes:
                            raise StepManiaError(
                                "ZIP extraction exceeded max_extracted_bytes",
                                permanent=True,
                            )
                        destination.write(chunk)

    def _move_extracted_files(self, source_root: Path, target_root: Path) -> Dict[Path, Path]:
        """Move extracted files into the pack directory, preserving relative paths."""
        target_root.mkdir(parents=True, exist_ok=True)
        moved: Dict[Path, Path] = {}
        for source in sorted(source_root.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            rel = source.relative_to(source_root)
            target = target_root.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = self._unique_path(target)
            source.replace(target)
            moved[rel] = target
        return moved

    def _select_content_files(self, root: Path, settings: StepManiaSettings) -> List[Path]:
        include_all = "*" in settings.include_extensions
        include = {item.lower() for item in settings.include_extensions if item != "*"}
        exclude = {item.lower() for item in settings.exclude_extensions}
        selected: List[Path] = []

        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if "__MACOSX" in rel_parts or any(part.startswith(".") for part in rel_parts):
                continue
            suffix = path.suffix.lower()
            if suffix in exclude:
                continue
            if not include_all and suffix not in include:
                continue
            selected.append(path)

        return selected

    def _song_label(self, path: Path, pack_dir: Path) -> str:
        try:
            rel_parts = path.relative_to(pack_dir).parts[:-1]
        except ValueError:
            return ""
        lowered = [part.lower() for part in rel_parts]
        if "songs" in lowered:
            index = lowered.index("songs")
            if index + 1 < len(rel_parts):
                return rel_parts[index + 1]
        return rel_parts[-1] if rel_parts else ""

    def _output_titles(
        self,
        pack_dir: Path,
        candidate: PackCandidate,
        output_files: List[Path],
    ) -> Dict[str, str]:
        titles: Dict[str, str] = {}
        for path in output_files:
            output_path = str(path.resolve())
            song = self._song_label(path, pack_dir)
            if song:
                title = f"{candidate.name} - {song} - {path.name}"
            else:
                title = f"{candidate.name} - {path.name}"
            titles[output_path] = title
        return titles

    def _result_metadata(
        self,
        source: MediaSource,
        candidate: PackCandidate,
        settings: StepManiaSettings,
        output_paths: List[str],
        output_titles: Dict[str, str],
        *,
        already_archived: bool = False,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = dict(source.metadata or {})
        pipeline_options = {
            "generic_files_enabled": True,
            "generic_file_accept": list(settings.generic_file_accept),
            "generic_file_reject": list(settings.generic_file_reject),
            **settings.pipeline_options,
        }
        metadata.update(
            {
                "output_paths": list(output_paths),
                "output_titles": dict(output_titles),
                "pipeline_options": pipeline_options,
                "stepmania_pack": candidate.to_dict(),
                "already_archived": already_archived,
            }
        )
        return metadata

    def _cached_result(
        self,
        source: MediaSource,
        candidate: PackCandidate,
        pack_dir: Path,
        marker: Dict[str, Any],
        settings: StepManiaSettings,
    ) -> ArchiveResult:
        raw_paths = marker.get("output_paths") or []
        paths = [str(path) for path in raw_paths if isinstance(path, str) and Path(path).is_file()]
        raw_titles = marker.get("output_titles")
        titles = raw_titles if isinstance(raw_titles, dict) else {}

        if not paths:
            try:
                existing = self._select_content_files(pack_dir, settings)
            except Exception:
                existing = []
            if existing:
                paths = [str(path.resolve()) for path in existing]
                titles = self._output_titles(pack_dir, candidate, existing)

        if paths:
            file_size = sum(Path(path).stat().st_size for path in paths if Path(path).exists())
            return ArchiveResult(
                success=True,
                output_path=paths[0],
                file_size=file_size,
                metadata=self._result_metadata(
                    source,
                    candidate,
                    settings,
                    paths,
                    {str(k): str(v) for k, v in titles.items() if str(k) in paths},
                    already_archived=True,
                ),
            )

        return ArchiveResult(
            success=True,
            output_path="",
            file_size=0,
            metadata=self._result_metadata(
                source,
                candidate,
                settings,
                [],
                {},
                already_archived=True,
            ),
        )
