"""ProwlarrPlugin: archive anything a Prowlarr indexer can find.

Discovery runs saved (or per-job) Prowlarr searches; archiving turns each
release into local files by fetching it directly, through Prowlarr's
download proxy, or via a torrent/Usenet client, then hands every file to
the Haven pipeline (Filecoin/IPFS upload + Arkiv record).

The plugin is content-agnostic: which indexers, categories, file types
and destinations are used is configuration. See ``docs/prowlarr.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from haven_cli.acquisition.bencode import BencodeError, parse_torrent
from haven_cli.acquisition.clients import (
    ClientError,
    ClientStatus,
    DownloadClient,
    NzbPayload,
    TorrentPayload,
)
from haven_cli.acquisition.clients.internal_torrent import InternalTorrentClient, torrent_label
from haven_cli.acquisition.clients.watch import WatchDirectory
from haven_cli.acquisition.http_fetch import (
    FetchedFile,
    FetchError,
    FetchPolicy,
    open_guarded,
    safe_filename,
    save_response,
)
from haven_cli.acquisition.importer import import_files
from haven_cli.acquisition.selection import SelectionPolicy, select_files
from haven_cli.acquisition.state import AcquisitionRecord, AcquisitionStore
from haven_cli.media.filetype import media_kind
from haven_cli.plugins.base import (
    ArchiveResult,
    ArchiverPlugin,
    MediaSource,
    PluginCapability,
    PluginInfo,
)
from haven_cli.plugins.builtin.prowlarr.settings import (
    ProwlarrSettings,
    SearchSpec,
    SettingsError,
    build_search,
    load_settings,
    render_template,
)
from haven_cli.services.prowlarr import (
    ALL_TORRENT_INDEXERS,
    ALL_USENET_INDEXERS,
    ProwlarrCategory,
    ProwlarrClient,
    ProwlarrError,
    ProwlarrIndexer,
    ProwlarrRelease,
    ProwlarrTag,
    resolve_redirect,
)
from haven_cli.services.url_safety import describe_url, public_url

logger = logging.getLogger(__name__)

MEDIA_TYPE = "prowlarr"


# ── Release (de)serialization ────────────────────────────────────────────


def release_to_dict(release: ProwlarrRelease) -> dict[str, Any]:
    data = asdict(release)
    data["publish_date"] = release.publish_date.isoformat() if release.publish_date else None
    data["categories"] = [[c.id, c.name] for c in release.categories]
    data["indexer_flags"] = list(release.indexer_flags)
    return data


def release_from_dict(data: dict[str, Any]) -> ProwlarrRelease:
    values = dict(data)
    published = values.get("publish_date")
    values["publish_date"] = datetime.fromisoformat(published) if published else None
    values["categories"] = tuple(
        ProwlarrCategory(int(i), str(n)) for i, n in values.get("categories") or []
    )
    values["indexer_flags"] = tuple(values.get("indexer_flags") or ())
    known = ProwlarrRelease.__dataclass_fields__
    return ProwlarrRelease(**{k: v for k, v in values.items() if k in known})


def source_key(release: ProwlarrRelease) -> str:
    """Stable id across runs and indexers (info-hash when known)."""
    if release.info_hash and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", release.info_hash):
        return f"prowlarr:btih:{release.info_hash}"
    digest = hashlib.sha256(f"{release.indexer_id}\x00{release.guid}".encode()).hexdigest()[:32]
    return f"prowlarr:{release.indexer_id}:{digest}"


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


#: Source keys currently being archived in this process. Guards against two
#: concurrent archive() calls (e.g. two jobs sharing a release) submitting
#: the same release twice.
_IN_FLIGHT: set[str] = set()

#: Finished records are only kept for diagnostics (``haven prowlarr pending --all``).
_DONE_RETENTION_S = 7 * 24 * 3600


@dataclass
class _Outcome:
    """Internal result of one acquisition attempt."""

    files: list[Path] | None = None
    pending: str = ""
    error: str = ""
    permanent: bool = False
    retry_after: float | None = None


# ── Plugin ───────────────────────────────────────────────────────────────


class ProwlarrPlugin(ArchiverPlugin):
    """Search Prowlarr indexers on a schedule and archive the results."""

    supports_concurrent_archive = True

    #: Optional httpx transport for every HTTP request the plugin makes
    #: (Prowlarr API and downloads). ``None`` uses the network; tests set an
    #: in-memory ``httpx.MockTransport``.
    http_transport: Any = None

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._settings_cache: ProwlarrSettings | None = None
        self._specs_by_key: dict[str, SearchSpec] = {}

    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name="ProwlarrPlugin",
            display_name="Prowlarr Archiver",
            version="1.0.0",
            description=(
                "Runs saved Prowlarr searches across any configured indexers and archives "
                "matching releases (direct files, torrents or Usenet) through the Haven pipeline."
            ),
            author="Haven",
            media_types=[MEDIA_TYPE],
            capabilities=[
                PluginCapability.DISCOVER,
                PluginCapability.ARCHIVE,
                PluginCapability.SEARCH,
                PluginCapability.METADATA,
                PluginCapability.HEALTH_CHECK,
            ],
            config_schema={
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "default": "http://localhost:9696"},
                    "api_key_env": {"type": "string", "default": "PROWLARR_API_KEY"},
                    "api_key_file": {"type": "string"},
                    "download_dir": {"type": "string"},
                    "searches": {"type": "array", "items": {"type": "object"}},
                },
            },
        )

    # ── configuration ──

    def configure(self, config: dict[str, Any]) -> None:
        super().configure(config)
        self._settings_cache = None

    def settings(self) -> ProwlarrSettings:
        """Validated settings (cached until :meth:`configure`)."""
        if self._settings_cache is None:
            data_dir: Path | None = None
            try:
                from haven_cli.config import get_config

                data_dir = Path(get_config().data_dir)
            except Exception:  # noqa: BLE001 - config is optional in tests/embedding
                data_dir = None
            self._settings_cache = load_settings(self._config, data_dir=data_dir)
        return self._settings_cache

    def validate_config(self) -> list[str]:
        try:
            settings = self.settings()
        except SettingsError as exc:
            return [str(exc)]
        errors: list[str] = []
        if not settings.api_key:
            errors.append(
                "Prowlarr API key not found: set $PROWLARR_API_KEY (or api_key_env / api_key_file)"
            )
        return errors

    async def initialize(self) -> None:
        try:
            settings = self.settings()
        except SettingsError as exc:
            # Surface through health_check so the scheduler reports it.
            logger.error("ProwlarrPlugin configuration error: %s", exc)
        else:
            for warning in settings.warnings:
                logger.warning("ProwlarrPlugin: %s", warning)
            settings.download_dir.mkdir(parents=True, exist_ok=True)
        await super().initialize()

    def _client(self, settings: ProwlarrSettings) -> ProwlarrClient:
        return ProwlarrClient(
            settings.base_url,
            settings.api_key,
            timeout=settings.timeout,
            verify=settings.verify,
            transport=self.http_transport,
        )

    async def health_check(self) -> bool:
        """Raises with a descriptive message when unusable (the scheduler logs it)."""
        settings = self.settings()
        if not settings.api_key:
            raise RuntimeError(
                "Prowlarr API key not found: set $PROWLARR_API_KEY (or api_key_env / api_key_file)"
            )
        async with self._client(settings) as client:
            await client.system_status()
        return self._enabled

    # ── discovery ──

    def specs_for(self, options: dict[str, Any] | None) -> list[SearchSpec]:
        """Searches to run for a job.

        Job options (``haven jobs create --option``):

        * ``prowlarr_searches``: names of saved searches (list or comma string).
        * ``prowlarr_search``: an inline search table (same keys as a saved
          search; ``name`` optional).

        Without either, every enabled saved search runs.
        """
        settings = self.settings()
        options = options or {}
        specs: list[SearchSpec] = []
        inline = options.get("prowlarr_search")
        if inline:
            if isinstance(inline, str):
                try:
                    inline = json.loads(inline)
                except ValueError as exc:
                    raise SettingsError(f"prowlarr_search: invalid JSON: {exc}") from exc
            rows = inline if isinstance(inline, list) else [inline]
            for index, row in enumerate(rows):
                row = {"name": f"job-{index}", **row} if isinstance(row, dict) else row
                specs.append(
                    build_search(row, settings.defaults, where=f"prowlarr_search[{index}]")
                )
        names = options.get("prowlarr_searches")
        if names:
            if isinstance(names, str):
                names = [n.strip() for n in names.split(",") if n.strip()]
            for name in names:
                spec = settings.search(str(name))
                if spec is None:
                    raise SettingsError(f"prowlarr_searches: no saved search named {name!r}")
                specs.append(spec)
        if not inline and not names:
            specs = [s for s in settings.searches if s.enabled]
        return specs

    @classmethod
    def from_haven_config(cls) -> ProwlarrPlugin:
        """Instance configured from ``[plugins.settings.ProwlarrPlugin]``."""
        from haven_cli.config import get_config

        return cls(config=get_config().plugins.plugin_settings.get("ProwlarrPlugin", {}))

    @classmethod
    def validate_job_options(cls, options: dict[str, Any]) -> list[str]:
        """Problems with a job's ``prowlarr_*`` options (used by ``haven jobs create``)."""
        if not any(k in options for k in ("prowlarr_search", "prowlarr_searches")):
            return []
        try:
            specs = cls.from_haven_config().specs_for(options)
        except SettingsError as exc:
            return [str(exc)]
        return [] if specs else ["job options select no searches"]

    async def discover_sources(self) -> list[MediaSource]:
        return await self.discover_sources_for({})

    async def discover_sources_for(self, options: dict[str, Any]) -> list[MediaSource]:
        settings = self.settings()
        specs = self.specs_for(options)
        store = AcquisitionStore(settings.state_file)
        await store.prune(done_older_than=_DONE_RETENTION_S, failed_older_than=0)
        records = {r.key: r for r in await store.all()}
        now = time.time()
        sources: dict[str, MediaSource] = {}

        if specs:
            async with self._client(settings) as client:
                indexers = await client.indexers()
                tags = await client.tags() if any(s.indexer_tags for s in specs) else []
                for spec in specs:
                    try:
                        releases = await self.run_search(client, spec, indexers, tags)
                    except (ProwlarrError, SettingsError) as exc:
                        logger.error("Prowlarr search '%s' failed: %s", spec.name, exc)
                        continue
                    by_id = {ix.id: ix for ix in indexers}
                    for release in releases:
                        key = source_key(release)
                        record = records.get(key)
                        if record is not None and record.status == "failed":
                            continue
                        if (
                            record is not None
                            and record.status == "retry"
                            and record.next_attempt_at > now
                        ):
                            continue
                        if key not in sources:
                            sources[key] = self._to_source(
                                release, spec, by_id.get(release.indexer_id)
                            )
                        if len(sources) >= settings.max_sources_per_run:
                            break

        # Re-surface in-flight downloads whose releases dropped out of results.
        for record in records.values():
            if record.status == "pending" and record.key not in sources and record.source:
                try:
                    sources[record.key] = MediaSource(**record.source)
                except TypeError:
                    continue
        logger.info("Prowlarr discovery: %d source(s) from %d search(es)", len(sources), len(specs))
        return list(sources.values())

    async def resolve_indexer_ids(
        self,
        spec: SearchSpec,
        indexers: list[ProwlarrIndexer],
        tags: list[ProwlarrTag],
    ) -> list[int]:
        """Indexer ids for *spec*; ``[]`` means every enabled indexer."""
        ids: set[int] = set(spec.indexer_ids)
        if spec.indexers:
            wanted = {n.lower() for n in spec.indexers}
            matched = {
                ix.id
                for ix in indexers
                if ix.name.lower() in wanted or ix.definition_name.lower() in wanted
            }
            missing = wanted - {
                n
                for ix in indexers
                for n in (ix.name.lower(), ix.definition_name.lower())
                if ix.id in matched
            }
            if missing:
                raise SettingsError(
                    f"search '{spec.name}': unknown indexer name(s) {', '.join(sorted(missing))}"
                )
            ids |= matched
        if spec.indexer_tags:
            labels = {t.label.lower(): t.id for t in tags}
            unknown = [t for t in spec.indexer_tags if t.lower() not in labels]
            if unknown:
                raise SettingsError(
                    f"search '{spec.name}': unknown indexer tag(s) {', '.join(unknown)}"
                )
            tag_ids = {labels[t.lower()] for t in spec.indexer_tags}
            ids |= {ix.id for ix in indexers if tag_ids & set(ix.tags)}
            if not ids:
                raise SettingsError(
                    f"search '{spec.name}': no indexers carry tag(s) {', '.join(spec.indexer_tags)}"
                )
        if spec.protocol != "any":
            if ids:
                by_id = {ix.id: ix for ix in indexers}
                ids = {i for i in ids if i in by_id and by_id[i].protocol == spec.protocol}
                if not ids:
                    raise SettingsError(
                        f"search '{spec.name}': no selected indexer uses protocol {spec.protocol}"
                    )
            else:
                return [ALL_TORRENT_INDEXERS if spec.protocol == "torrent" else ALL_USENET_INDEXERS]
        return sorted(ids)

    async def run_search(
        self,
        client: ProwlarrClient,
        spec: SearchSpec,
        indexers: list[ProwlarrIndexer],
        tags: list[ProwlarrTag],
    ) -> list[ProwlarrRelease]:
        """Execute *spec* (with paging), then filter, sort, dedupe and cap."""
        indexer_ids = await self.resolve_indexer_ids(spec, indexers, tags)
        query = spec.compiled_query()
        collected: list[ProwlarrRelease] = []
        for page in range(spec.pages):
            batch = await client.search(
                query,
                search_type=spec.type,
                indexer_ids=indexer_ids,
                categories=spec.categories,
                limit=spec.limit,
                offset=page * spec.limit if page else None,
            )
            collected.extend(batch)
            if len(batch) < spec.limit:
                break
        return select_releases(collected, spec)

    def _to_source(
        self, release: ProwlarrRelease, spec: SearchSpec, indexer: ProwlarrIndexer | None
    ) -> MediaSource:
        key = source_key(release)
        fields = {**release.as_template_fields(), "search": spec.name, "file": ""}
        title = render_template(spec.title_template, fields) or release.title
        creator = render_template(spec.creator_template, fields) if spec.creator_template else ""
        private = indexer.is_private if indexer is not None else True
        publish = spec.publish_source if spec.publish_source is not None else not private
        uri = ""
        if publish:
            for candidate in (release.info_url, release.comment_url, release.guid):
                # Private indexers routinely embed passkeys in URL paths;
                # publish only URLs without token-like path segments.
                uri = public_url(candidate, reject_token_paths=private) or ""
                if uri:
                    break
        metadata: dict[str, Any] = {
            **spec.pipeline_options,
            "title": title,
            "generic_files_enabled": True,
            "generic_file_accept": list(spec.accept),
            "generic_file_reject": list(spec.reject),
            "prowlarr_release": release_to_dict(release),
            "prowlarr_spec": spec.to_dict(),
        }
        if creator:
            metadata["creator_handle"] = creator
        include_meta = (
            spec.arkiv_release_meta if spec.arkiv_release_meta is not None else not private
        )
        if include_meta:
            extra: dict[str, Any] = {"idx": release.indexer, "prot": release.protocol}
            if release.publish_date:
                extra["pub"] = release.publish_date.isoformat()
            if release.categories:
                extra["cat"] = sorted(release.category_ids())
            metadata["arkiv_payload_extra"] = {**extra, **metadata.get("arkiv_payload_extra", {})}
        return MediaSource(
            source_id=key,
            media_type=MEDIA_TYPE,
            uri=uri,
            title=title,
            priority=spec.priority,
            metadata=metadata,
        )

    # ── archiving ──

    async def archive(self, source: MediaSource) -> ArchiveResult:
        if source.media_type != MEDIA_TYPE:
            return ArchiveResult(
                success=False, error=f"Unsupported media type: {source.media_type}"
            )
        try:
            settings = self.settings()
            release = release_from_dict(source.metadata["prowlarr_release"])
            spec_data = dict(source.metadata["prowlarr_spec"])
        except (KeyError, TypeError, ValueError, SettingsError) as exc:
            return ArchiveResult(success=False, error=f"Invalid Prowlarr source: {exc}")
        spec = SearchSpec(
            **{k: v for k, v in spec_data.items() if k in SearchSpec.__dataclass_fields__}
        )
        key = source.source_id
        store = AcquisitionStore(settings.state_file)
        source_dict = {
            "source_id": source.source_id,
            "media_type": source.media_type,
            "uri": source.uri,
            "title": source.title,
            "priority": source.priority,
            "metadata": source.metadata,
        }
        if key in _IN_FLIGHT:
            return ArchiveResult(
                success=False, error="pending: already being archived", metadata={"pending": True}
            )
        _IN_FLIGHT.add(key)
        try:
            return await self._archive_claimed(
                settings, spec, release, key, store, source, source_dict
            )
        finally:
            _IN_FLIGHT.discard(key)

    async def _archive_claimed(
        self,
        settings: ProwlarrSettings,
        spec: SearchSpec,
        release: ProwlarrRelease,
        key: str,
        store: AcquisitionStore,
        source: MediaSource,
        source_dict: dict[str, Any],
    ) -> ArchiveResult:
        record = await store.get(key)
        if record is not None and record.status == "failed":
            return ArchiveResult(
                success=False, error=f"Previously failed permanently: {record.last_error}"
            )

        try:
            outcome = await self._acquire(settings, spec, release, key, record, store, source_dict)
        except (ProwlarrError, FetchError, ClientError, OSError, SettingsError) as exc:
            if _is_config_error(exc):
                # A setup problem, not this release's fault: report it without
                # recording a failure so the release is retried once fixed.
                logger.error(
                    "Prowlarr archive for '%s' blocked by configuration: %s", release.title, exc
                )
                return ArchiveResult(success=False, error=f"configuration: {exc}")
            permanent = bool(getattr(exc, "permanent", False)) or (
                isinstance(exc, ProwlarrError) and not exc.transient and exc.code != "not_found"
            )
            outcome = _Outcome(
                error=str(exc), permanent=permanent, retry_after=getattr(exc, "retry_after", None)
            )

        if outcome.pending:
            return ArchiveResult(
                success=False, error=f"pending: {outcome.pending}", metadata={"pending": True}
            )
        if outcome.error or not outcome.files:
            message = outcome.error or "no files matched the accepted types"
            updated = await store.record_failure(
                key,
                message,
                permanent=outcome.permanent or not outcome.error,
                max_attempts=settings.max_attempts,
                base_backoff=max(settings.retry_backoff, outcome.retry_after or 0.0),
                source=source_dict,
            )
            logger.warning(
                "Prowlarr archive failed for '%s' (%s, attempt %d): %s",
                release.title,
                updated.status,
                updated.attempts,
                message,
            )
            return ArchiveResult(success=False, error=message)

        files = self._finalize_files(settings, spec, key, source.title, outcome.files)
        await store.put(AcquisitionRecord(key=key, status="done", source={}, attempts=0))
        titles = self._titles(spec, release, source.title, files)
        return ArchiveResult(
            success=True,
            output_path=str(files[0]),
            file_size=sum(p.stat().st_size for p in files if p.exists()),
            metadata={
                "output_paths": [str(p) for p in files],
                "output_titles": titles,
                "prowlarr_key": key,
            },
        )

    def _titles(
        self, spec: SearchSpec, release: ProwlarrRelease, title: str, files: list[Path]
    ) -> dict[str, str]:
        if len(files) == 1:
            return {str(files[0]): title}
        titles: dict[str, str] = {}
        fields = {**release.as_template_fields(), "search": spec.name}
        for path in files:
            if "{file}" in spec.title_template:
                rendered = render_template(spec.title_template, {**fields, "file": path.name})
            else:
                rendered = f"{title} - {path.name}"
            titles[str(path)] = rendered or path.name
        return titles

    def _finalize_files(
        self, settings: ProwlarrSettings, spec: SearchSpec, key: str, title: str, files: list[Path]
    ) -> list[Path]:
        if len(files) <= 1 or spec.multi_file == "each":
            return files
        if spec.multi_file == "largest":
            return [max(files, key=lambda p: p.stat().st_size)]
        # tar: one archive per release, relative names from the common root.
        pack_dir = settings.download_dir / "packs"
        pack_dir.mkdir(parents=True, exist_ok=True)
        target = pack_dir / f"{safe_filename(title, default=_digest(key))}.tar"
        try:
            common = Path(os.path.commonpath([str(p) for p in files]))
            root = common if common.is_dir() else common.parent
        except ValueError:
            root = files[0].parent
        tmp = target.with_suffix(".tar.part")
        with tarfile.open(tmp, "w") as archive:
            for path in files:
                try:
                    arcname = str(path.relative_to(root))
                except ValueError:
                    arcname = path.name
                archive.add(path, arcname=arcname, recursive=False)
        tmp.replace(target)
        return [target]

    def _selection(self, spec: SearchSpec, settings: ProwlarrSettings) -> SelectionPolicy:
        return SelectionPolicy(
            accept=tuple(spec.accept),
            reject=tuple(spec.reject),
            max_size=settings.max_file_bytes,
            mode=spec.select_mode,
        )

    def _fetch_policy(self, spec: SearchSpec, settings: ProwlarrSettings) -> FetchPolicy:
        return FetchPolicy(
            max_bytes=settings.max_file_bytes,
            timeout=settings.fetch_timeout,
            allowed_hosts=tuple(spec.allowed_hosts),
            allow_private_hosts=settings.allow_private_hosts,
            verify=settings.verify,
            transport=self.http_transport,
        )

    def direct_url(self, spec: SearchSpec, release: ProwlarrRelease) -> str | None:
        """URL for ``fetch = "direct"``: ``link_field``, rewritten by ``link_pattern``."""
        value = getattr(release, spec.link_field, None)
        if not isinstance(value, str) or not value:
            return None
        if spec.link_pattern:
            rewritten, count = re.subn(spec.link_pattern, spec.link_replacement, value, count=1)
            return rewritten if count else None
        return value

    def _strategy(self, spec: SearchSpec, release: ProwlarrRelease) -> str:
        if spec.fetch != "auto":
            return spec.fetch
        if spec.link_pattern:
            return "direct"
        if release.protocol == "torrent" and spec.torrent_client == "prowlarr":
            return "grab"
        if release.protocol == "usenet" and spec.usenet_client == "prowlarr":
            return "grab"
        if release.download_url or release.magnet_url:
            return "prowlarr"
        return "direct"

    async def _acquire(
        self,
        settings: ProwlarrSettings,
        spec: SearchSpec,
        release: ProwlarrRelease,
        key: str,
        record: AcquisitionRecord | None,
        store: AcquisitionStore,
        source_dict: dict[str, Any],
    ) -> _Outcome:
        # Resume an in-flight download.
        if record is not None and record.status == "pending" and record.backend:
            backend = self._backend(record.backend, spec, settings)
            try:
                return await self._wait(settings, spec, backend, record, store, key)
            finally:
                await backend.aclose()

        strategy = self._strategy(spec, release)
        work_dir = settings.download_dir / "files" / _digest(key)

        if strategy == "grab":
            return await self._grab(settings, spec, release, key, store, source_dict)

        pointer: FetchedFile | str | None
        if strategy == "direct":
            url = self.direct_url(spec, release)
            if not url:
                return _Outcome(
                    error=f"release has no usable {spec.link_field}"
                    + (" matching link_pattern" if spec.link_pattern else ""),
                    permanent=True,
                )
            pointer = await self._fetch_direct(url, work_dir, spec, settings, release)
        else:
            pointer = await self._fetch_via_prowlarr(work_dir, spec, settings, release)

        if isinstance(pointer, str):  # magnet
            return await self._submit(
                settings, spec, key, store, source_dict, TorrentPayload(magnet=pointer), release
            )

        kind = media_kind(pointer.mime)
        if kind == "torrent":
            data = pointer.path.read_bytes()
            pointer.path.unlink(missing_ok=True)
            try:
                meta = parse_torrent(data)
            except BencodeError as exc:
                return _Outcome(error=f"invalid .torrent from indexer: {exc}", permanent=True)
            payload: Any = TorrentPayload(torrent=data, info_hash=meta.info_hash or None)
            return await self._submit(settings, spec, key, store, source_dict, payload, release)
        if kind == "nzb":
            data = pointer.path.read_bytes()
            pointer.path.unlink(missing_ok=True)
            return await self._submit(
                settings,
                spec,
                key,
                store,
                source_dict,
                NzbPayload(nzb=data, filename=f"{_digest(key)}.nzb"),
                release,
            )
        # Content file: type check only (no name-based exclusions for a single download).
        single = SelectionPolicy(
            accept=tuple(spec.accept), reject=tuple(spec.reject), exclude_patterns=()
        )
        files = select_files(pointer.path, single)
        if not files:
            pointer.path.unlink(missing_ok=True)
            return _Outcome(
                error=f"downloaded file is {pointer.mime}, which accept/reject does not allow",
                permanent=True,
            )
        return _Outcome(files=files)

    async def _fetch_direct(
        self,
        url: str,
        work_dir: Path,
        spec: SearchSpec,
        settings: ProwlarrSettings,
        release: ProwlarrRelease,
    ) -> FetchedFile | str:
        if url.lower().startswith("magnet:"):
            return url
        policy = self._fetch_policy(spec, settings)
        response, final_url = await open_guarded(
            url, policy, min_interval=settings.min_request_interval
        )
        if response is None:
            return final_url
        return await save_response(
            response,
            work_dir,
            filename_hint=safe_filename(release.title),
            max_bytes=policy.max_bytes,
            final_url=final_url,
        )

    async def _fetch_via_prowlarr(
        self,
        work_dir: Path,
        spec: SearchSpec,
        settings: ProwlarrSettings,
        release: ProwlarrRelease,
    ) -> FetchedFile | str:
        link = release.download_url or release.magnet_url
        if not link:
            raise FetchError("release has no download link", permanent=True)
        if link.lower().startswith("magnet:"):
            return link
        policy = self._fetch_policy(spec, settings)
        async with self._client(settings) as client:
            if not client.proxy_url(link) and not client.is_prowlarr_origin(link):
                # Not a proxy link (unusual): fetch it like any third-party URL.
                return await self._fetch_direct(link, work_dir, spec, settings, release)
            response = await client.open_download(link, timeout=settings.fetch_timeout)
            try:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    await response.aclose()
                    if not location:
                        raise FetchError("Prowlarr redirect without Location", permanent=True)
                    target = resolve_redirect(str(response.url), location)
                    logger.debug(
                        "Prowlarr redirected '%s' to %s", release.title, describe_url(target)
                    )
                    return await self._fetch_direct(target, work_dir, spec, settings, release)
                if response.status_code in (401, 403):
                    await response.aclose()
                    raise ProwlarrError(
                        "Prowlarr rejected the API key for downloads "
                        f"(HTTP {response.status_code})",
                        "unauthorized",
                        status=response.status_code,
                    )
                if response.status_code >= 400:
                    body = (await response.aread())[:500].decode("utf-8", "replace")
                    await response.aclose()
                    retry_after = None
                    if response.headers.get("retry-after", "").isdigit():
                        retry_after = float(response.headers["retry-after"])
                    status = response.status_code
                    transient = status in (408, 429) or status >= 500
                    detail = re.search(r'description="([^"]*)"', body)
                    raise FetchError(
                        client.redact(
                            f"Prowlarr download failed (HTTP {status})"
                            + (f": {detail.group(1)}" if detail else "")
                        ),
                        permanent=not transient,
                        retry_after=retry_after,
                    )
                return await save_response(
                    response,
                    work_dir,
                    filename_hint=safe_filename(release.title),
                    max_bytes=policy.max_bytes,
                    final_url=describe_url(str(response.url)),
                )
            except BaseException:
                await response.aclose()
                raise

    def _backend(self, name: str, spec: SearchSpec, settings: ProwlarrSettings) -> DownloadClient:
        from haven_cli.plugins.builtin.prowlarr.backends import make_backend

        return make_backend(name, settings, self._selection(spec, settings))

    async def _submit(
        self,
        settings: ProwlarrSettings,
        spec: SearchSpec,
        key: str,
        store: AcquisitionStore,
        source_dict: dict[str, Any],
        payload: TorrentPayload | NzbPayload,
        release: ProwlarrRelease,
    ) -> _Outcome:
        if isinstance(payload, TorrentPayload):
            name = spec.torrent_client
            if name == "none":
                return _Outcome(
                    error="release is a torrent but torrent_client = 'none'", permanent=True
                )
        else:
            name = spec.usenet_client
            if name == "none":
                return _Outcome(
                    error="release is an NZB but usenet_client = 'none'", permanent=True
                )
        if name == "prowlarr":
            return await self._grab(settings, spec, release, key, store, source_dict)
        backend = self._backend(name, spec, settings)
        try:
            handle = await backend.submit(payload, label=torrent_label(key), title=release.title)
            record = AcquisitionRecord(
                key=key, backend=name, handle=handle, status="pending", source=source_dict
            )
            await store.put(record)
            logger.info("Submitted '%s' to %s", release.title, name)
            return await self._wait(settings, spec, backend, record, store, key)
        finally:
            await backend.aclose()

    async def _grab(
        self,
        settings: ProwlarrSettings,
        spec: SearchSpec,
        release: ProwlarrRelease,
        key: str,
        store: AcquisitionStore,
        source_dict: dict[str, Any],
    ) -> _Outcome:
        if settings.watch_dir is None:
            return _Outcome(error="grabbing through Prowlarr needs watch_dir", permanent=True)
        async with self._client(settings) as client:
            await client.grab(release, spec.download_client_id or None)
        watcher = WatchDirectory(settings.watch_dir, settle_seconds=settings.watch_settle)
        handle = await watcher.submit(None, label=torrent_label(key), title=release.title)
        record = AcquisitionRecord(
            key=key, backend="watch", handle=handle, status="pending", source=source_dict
        )
        await store.put(record)
        logger.info("Grabbed '%s' via Prowlarr; watching %s", release.title, settings.watch_dir)
        return await self._wait(settings, spec, watcher, record, store, key)

    async def _wait(
        self,
        settings: ProwlarrSettings,
        spec: SearchSpec,
        backend: DownloadClient,
        record: AcquisitionRecord,
        store: AcquisitionStore,
        key: str,
    ) -> _Outcome:
        deadline = time.monotonic() + settings.wait_timeout
        status: ClientStatus
        while True:
            status = await backend.status(record.handle)
            if status.done:
                break
            if status.state == "failed":
                await store.remove(key)
                return _Outcome(
                    error=f"{backend.name}: {status.error or 'download failed'}", permanent=True
                )
            if status.state == "missing":
                await store.remove(key)
                return _Outcome(error=f"{backend.name}: {status.error or 'download disappeared'}")
            if time.monotonic() >= deadline:
                return _Outcome(pending=f"{backend.name} {status.state} {status.progress:.0%}")
            await asyncio.sleep(settings.poll_interval)

        selection = self._selection(spec, settings)
        if status.files:
            candidates = [p for p in status.files if p.is_file()]
            files = [p for f in candidates for p in select_files(f, selection)]
            if selection.mode == "largest" and files:
                files = [max(files, key=lambda p: p.stat().st_size)]
        elif status.content_path is not None and status.content_path.exists():
            files = select_files(status.content_path, selection)
        else:
            return _Outcome(
                error=f"{backend.name} reported completion but {status.content_path} is missing"
            )
        if not files:
            return _Outcome(
                error="completed download has no files matching accept/reject", permanent=True
            )

        internal = isinstance(backend, InternalTorrentClient)
        mode = spec.import_mode or (
            "move" if internal and not settings.torrent_seed else "hardlink"
        )
        await backend.release(record.handle)
        root = status.content_path if status.content_path and status.content_path.is_dir() else None
        imported = import_files(
            files, settings.download_dir / "imports" / _digest(key), mode=mode, root=root
        )
        if internal and mode == "move" and status.content_path is not None:
            shutil.rmtree(Path(record.handle.get("save_path", "")), ignore_errors=True)
        return _Outcome(files=imported)


def _is_config_error(exc: Exception) -> bool:
    if isinstance(exc, SettingsError):
        return True
    if isinstance(exc, ProwlarrError):
        return exc.code in ("unauthorized", "not_configured")
    if isinstance(exc, ClientError):
        return exc.config
    return False


def select_releases(releases: list[ProwlarrRelease], spec: SearchSpec) -> list[ProwlarrRelease]:
    """Apply a search's filters, sort, dedupe and cap (pure; used by tests)."""
    now = datetime.now(UTC)
    include = [re.compile(p, re.IGNORECASE) for p in spec.include]
    exclude = [re.compile(p, re.IGNORECASE) for p in spec.exclude]
    kept: list[ProwlarrRelease] = []
    for release in releases:
        if spec.max_age_hours:
            age: float | None
            if release.publish_date is not None:
                age = (now - release.publish_date).total_seconds() / 3600
            else:
                age = release.age_hours
            if age is None or age > spec.max_age_hours:
                continue
        if release.size is not None:
            if spec.min_size and release.size < spec.min_size:
                continue
            if spec.max_size and release.size > spec.max_size:
                continue
        if (
            spec.min_seeders
            and release.protocol == "torrent"
            and (release.seeders or 0) < spec.min_seeders
        ):
            continue
        if include and not all(p.search(release.title) for p in include):
            continue
        if exclude and any(p.search(release.title) for p in exclude):
            continue
        kept.append(release)

    if spec.sort != "relevance":

        def sort_key(r: ProwlarrRelease) -> Any:
            if spec.sort == "publish_date":
                return r.publish_date.timestamp() if r.publish_date else float("-inf")
            if spec.sort == "title":
                return r.title.lower()
            return getattr(r, spec.sort) or 0

        kept.sort(key=sort_key, reverse=spec.order == "desc")

    if spec.dedupe != "none":
        seen: set[Any] = set()
        unique: list[ProwlarrRelease] = []
        for release in kept:
            normalized = re.sub(r"[\W_]+", " ", release.title.lower()).strip()
            if spec.dedupe == "info_hash":
                ident: Any = release.info_hash or source_key(release)
            elif spec.dedupe == "title":
                ident = normalized
            elif spec.dedupe == "title_size":
                ident = (normalized, release.size)
            else:  # auto
                ident = release.info_hash or (normalized, release.size)
            if ident in seen:
                continue
            seen.add(ident)
            unique.append(release)
        kept = unique
    return kept[: spec.max_results]


__all__ = [
    "ProwlarrPlugin",
    "select_releases",
    "source_key",
    "release_to_dict",
    "release_from_dict",
]
