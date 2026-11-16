"""Provider implementations for the StepMania plugin.

The spec covers two complementary catalogs:

* Etterna Online exposes a public Typesense API with song-level documents
  and pack download URLs.
* StepMania Online exposes one CSV catalog dump and deterministic pack ZIP
  downloads.
"""

from __future__ import annotations

import csv
import io
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from haven_cli.acquisition.http_fetch import RATE_LIMITER
from haven_cli.plugins.builtin.stepmania.settings import (
    StepManiaSettings,
    version_tuple,
)

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """A provider request or response failed."""

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class PackCandidate:
    """A downloadable StepMania/Etterna pack."""

    provider: str
    pack_id: str
    name: str
    download_url: str
    song_count: int = 0
    size_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return f"{self.provider}:{self.pack_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "pack_id": self.pack_id,
            "name": self.name,
            "download_url": self.download_url,
            "song_count": self.song_count,
            "size_bytes": self.size_bytes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackCandidate":
        metadata = data.get("metadata")
        return cls(
            provider=str(data["provider"]),
            pack_id=str(data["pack_id"]),
            name=str(data.get("name") or f"Pack {data['pack_id']}"),
            download_url=str(data["download_url"]),
            song_count=int(data.get("song_count") or 0),
            size_bytes=int(data.get("size_bytes") or 0),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
        )


Unavailable = Callable[[PackCandidate], bool]


class EtternaProvider:
    """Random pack/song discovery through Etterna's Typesense API."""

    provider_name = "etterna"

    def __init__(self, settings: StepManiaSettings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    async def discover(
        self,
        *,
        count: int,
        unit: str,
        rng: random.Random,
        unavailable: Unavailable,
    ) -> list[PackCandidate]:
        candidates: list[PackCandidate] = []
        seen: set[str] = set()
        for _ in range(count):
            for _attempt in range(self.settings.etterna_random_attempts):
                candidate = await self._draw(unit=unit, rng=rng)
                if candidate is None:
                    continue
                if candidate.source_id in seen or unavailable(candidate):
                    continue
                seen.add(candidate.source_id)
                candidates.append(candidate)
                break
        return candidates

    async def _draw(self, *, unit: str, rng: random.Random) -> PackCandidate | None:
        if unit == "song":
            return await self._draw_from_song(rng)
        return await self._draw_pack(rng)

    async def _draw_pack(self, rng: random.Random) -> PackCandidate | None:
        pack_id = rng.randint(self.settings.etterna_pack_id_min, self.settings.etterna_pack_id_max)
        document = await self._search_document("packs", f"id:={pack_id}")
        if document is None or not self._pack_matches(document):
            return None
        return self._candidate_from_pack(document)

    async def _draw_from_song(self, rng: random.Random) -> PackCandidate | None:
        song_id = rng.randint(self.settings.etterna_song_id_min, self.settings.etterna_song_id_max)
        song = await self._search_document("songs", f"id:={song_id}")
        if song is None or not self._song_matches(song):
            return None

        packs = song.get("packs") or []
        if not packs:
            return None
        pack_ref = packs[rng.randrange(len(packs))]
        pack_id = pack_ref.get("id") if isinstance(pack_ref, dict) else pack_ref
        if pack_id is None:
            return None

        pack = await self._search_document("packs", f"id:={pack_id}")
        if pack is None or not self._pack_matches(pack):
            return None
        return self._candidate_from_pack(pack, seed_song=song)

    async def _search_document(
        self, collection: str, filter_by: str
    ) -> dict[str, Any] | None:
        url = f"{self.settings.etterna_base_url}/multi_search"
        payload = {
            "searches": [
                {
                    "collection": collection,
                    "q": "*",
                    "limit": 1,
                    "filter_by": filter_by,
                }
            ]
        }
        headers = {
            "X-TYPESENSE-API-KEY": self.settings.etterna_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        await RATE_LIMITER.wait(url, self.settings.request_interval_s)
        try:
            response = await self.client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Etterna {collection} request failed: {exc}") from exc
        if response.status_code != 200:
            transient = response.status_code == 429 or response.status_code >= 500
            raise ProviderError(
                f"Etterna {collection} search returned HTTP {response.status_code}",
                transient=transient,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Etterna returned invalid JSON", transient=False) from exc

        results = data.get("results") or []
        if not results:
            return None
        result = results[0]
        if int(result.get("found") or 0) != 1:
            return None
        hits = result.get("hits") or []
        if not hits:
            return None
        document = hits[0].get("document")
        return document if isinstance(document, dict) else None

    def _pack_matches(self, document: dict[str, Any]) -> bool:
        if self.settings.etterna_exclude_nsfw and document.get("nsfw"):
            return False
        overall = _as_float(document.get("overall"))
        if overall is not None:
            if (
                self.settings.etterna_min_overall is not None
                and overall < self.settings.etterna_min_overall
            ):
                return False
            if (
                self.settings.etterna_max_overall is not None
                and overall > self.settings.etterna_max_overall
            ):
                return False
        if self.settings.etterna_tags:
            tags = {str(tag).lower() for tag in document.get("tags") or []}
            wanted = {tag.lower() for tag in self.settings.etterna_tags}
            if not wanted.issubset(tags):
                return False
        return True

    def _song_matches(self, document: dict[str, Any]) -> bool:
        return not (self.settings.etterna_exclude_nsfw and document.get("nsfw"))

    def _candidate_from_pack(
        self, document: dict[str, Any], *, seed_song: dict[str, Any] | None = None
    ) -> PackCandidate | None:
        pack_id = document.get("id")
        download_url = document.get("download")
        if pack_id is None or not download_url:
            return None
        name = str(document.get("name") or f"Pack {pack_id}")
        metadata = {
            "etterna_pack": document,
            "provider_document": document,
        }
        if seed_song is not None:
            metadata["etterna_seed_song"] = seed_song
        return PackCandidate(
            provider=self.provider_name,
            pack_id=str(pack_id),
            name=name,
            download_url=str(download_url),
            song_count=_as_int(document.get("song_count")),
            size_bytes=_as_int(document.get("bytes")),
            metadata=metadata,
        )


class StepManiaOnlineProvider:
    """Uniform random pack discovery through SMO's CSV catalog."""

    provider_name = "stepmania_online"

    def __init__(self, settings: StepManiaSettings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self._catalog: list[PackCandidate] | None = None
        self._catalog_at = 0.0

    async def discover(
        self,
        *,
        count: int,
        unit: str,
        rng: random.Random,
        unavailable: Unavailable,
    ) -> list[PackCandidate]:
        if unit == "song":
            raise ProviderError(
                "stepmania_online does not support song-level random selection "
                "(the CSV catalog is pack-level only)",
                transient=False,
            )
        catalog = await self._catalog_candidates()
        eligible = [
            candidate
            for candidate in catalog
            if self._pack_matches(candidate) and not unavailable(candidate)
        ]
        if not eligible:
            return []
        return rng.sample(eligible, min(count, len(eligible)))

    async def _catalog_candidates(self) -> list[PackCandidate]:
        now = time.monotonic()
        if self._catalog is not None and now - self._catalog_at < self.settings.catalog_ttl_s:
            return self._catalog

        url = f"{self.settings.smo_base_url}/api/packs"
        await RATE_LIMITER.wait(url, self.settings.request_interval_s)
        try:
            response = await self.client.get(url)
        except httpx.HTTPError as exc:
            raise ProviderError(f"SMO catalog request failed: {exc}") from exc
        if response.status_code != 200:
            transient = response.status_code == 429 or response.status_code >= 500
            raise ProviderError(
                f"SMO catalog returned HTTP {response.status_code}", transient=transient
            )

        candidates: list[PackCandidate] = []
        try:
            reader = csv.DictReader(
                io.StringIO(response.text.lstrip("\ufeff")),
                skipinitialspace=True,
            )
            for row in reader:
                candidate = self._candidate_from_row(row)
                if candidate is not None:
                    candidates.append(candidate)
        except csv.Error as exc:
            raise ProviderError(f"SMO catalog CSV parse failed: {exc}", transient=False) from exc
        if not candidates:
            raise ProviderError("SMO catalog contained no packs", transient=False)

        self._catalog = candidates
        self._catalog_at = now
        logger.info("Loaded %d SMO pack candidates", len(candidates))
        return candidates

    def _candidate_from_row(self, row: dict[str, str]) -> PackCandidate | None:
        pack_id = str(row.get("ID") or "").strip()
        name = str(row.get("Pack Name") or "").strip()
        if not pack_id or not name:
            return None
        try:
            size_bytes = int(str(row.get("Size") or "0").strip() or 0)
        except ValueError:
            size_bytes = 0
        try:
            song_count = int(str(row.get("Song Count") or "0").strip() or 0)
        except ValueError:
            song_count = 0
        metadata = {
            "smo_row": dict(row),
            "pack_type": str(row.get("PackType") or "").strip().lower(),
            "substyle": str(row.get("Substyle") or "").strip(),
            "min_version": str(row.get("Min Version") or "").strip(),
        }
        return PackCandidate(
            provider=self.provider_name,
            pack_id=pack_id,
            name=name,
            download_url=f"{self.settings.smo_base_url}/download/pack/{pack_id}/",
            song_count=song_count,
            size_bytes=size_bytes,
            metadata=metadata,
        )

    def _pack_matches(self, candidate: PackCandidate) -> bool:
        metadata = candidate.metadata
        pack_type = str(metadata.get("pack_type") or "")
        if self.settings.smo_pack_types and pack_type not in self.settings.smo_pack_types:
            return False

        version = version_tuple(str(metadata.get("min_version") or ""))
        if self.settings.smo_min_version:
            wanted = version_tuple(self.settings.smo_min_version)
            if not version or version < wanted:
                return False
        if self.settings.smo_max_version:
            wanted = version_tuple(self.settings.smo_max_version)
            if not version or version > wanted:
                return False

        if (
            self.settings.smo_min_size_bytes
            and candidate.size_bytes < self.settings.smo_min_size_bytes
        ):
            return False
        if (
            self.settings.smo_max_size_bytes
            and candidate.size_bytes > self.settings.smo_max_size_bytes
        ):
            return False
        return True


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def provider_for(
    name: str, settings: StepManiaSettings, client: httpx.AsyncClient
) -> EtternaProvider | StepManiaOnlineProvider:
    """Return the provider implementation for *name*."""
    if name == "etterna":
        return EtternaProvider(settings, client)
    if name == "stepmania_online":
        return StepManiaOnlineProvider(settings, client)
    raise ProviderError(f"unknown StepMania provider: {name}", transient=False)
